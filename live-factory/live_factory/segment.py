"""Matching the recordings to the lineup: where each talk starts and ends - and whether it was recorded properly.

  brief()     one Markdown file per track for Claude (segment_brief_<track>.md): the running order with the titles and
              abstracts, then per recording: its pauses, the speakers introducing themselves ("Hey, I'm Dan"), the MC's
              introductions, the closing lines ("Thank you - any questions?"), names and title words heard, and the
              whole transcript. Claude reads it, looks at frames around the boundaries, and writes cuts.json.
  draft()     a first automatic guess at cuts.json from the same signals - a starting point, never approved by itself.
  assess()    the checks on one cut: is it the talk it says it is (its words against every title and abstract), and was
              it recorded properly (quiet or distorted sound, drop-outs, few words, an unsure transcript, a blank
              picture, much shorter or longer than its slot). Flags are shown in the dashboard; a flagged cut is never
              approved in bulk.
  validate()  hard checks of a cuts.json against the lineup and the recordings before anything is cut.

A talk runs from the speaker's first words ("Hey, I'm Dan" - not the MC's introduction) to their closing "thank you",
without the MC's thanks and the next set-up. The Q&A after it ("any questions?", then someone at the back without a
microphone) is cut off when it clearly is one (qa_split); questions in the middle of a talk, or any doubt, keep it.
Talks run 10-30 minutes, workshops longer.

cuts.json (in the event's work folder) - the single source the dashboard edits and `run` cuts from:
  {"cuts": [{"key": "<talk key>", "parts": [{"rec": "r1", "start": 812.4, "end": 2511.0}],
             "confidence": "high|medium|low", "reason": "...", "by": "draft|claude|marek", "approved": false,
             "flags": [...], "notes": [...]}],                      (flags / notes are written by assess())
   "skipped": [{"key": "<talk key>", "why": "not recorded / no-show / ..."}]}
A talk split across two recordings has two parts, in playing order.
"""
import difflib
import json
import os
import re
import statistics
import unicodedata

from . import analyse, events

# the MC handing over: "please welcome", "next up", "give it up for"
INTRO = re.compile(r"\b(welcome|next up|next speaker|up next|please|give it up|round of applause|introduce|on stage|"
                   r"joining us|over to|hand over|let's hear)\b", re.I)
# the speaker opening: "Hey, I'm Dan", "my name is Dan Smith" (on normalised text: "i'm" -> "i m")
SELF = r"\b(?:i m|im|i am|my name is|my name s|myself|i go by)\s+(?:%s)\b"
# the speaker closing: "thank you", "thanks everyone", "any questions?", "that's all I have"
# (published talks end on "So I think that is my time ... Thank you all very much", "Any other questions? Cool. Thank you")
CLOSE = re.compile(r"\b(thank you|thanks|that s all|that s it|that is all|that s my time|that is my time|out of time|"
                   r"happy to take|any questions|questions)\b")
QA = re.compile(r"\b(any questions|any other questions|questions|happy to take|great question|good question)\b")
# someone in the room asked, without an opener from the speaker (Stephen Atwell, SREday Seattle 2026: "Great question.")
ASKED = re.compile(r"\b(great question|good question|that s a great question|thanks for the question|to repeat the question)\b")
SELF_ANY = re.compile(r"\b(?:i m|im|i am|my name is|my name s|myself)\s+([a-z]+)(?:\s+([a-z]+))?")
# the speaker opening the Q&A: "any questions?", "do you have questions", "let's do Q&A", "happy to take questions"
QA_START = re.compile(r"^(ok |okay |so |any )?questions\b|\b(any questions|do you have (any )?questions|questions for me|(let s|we can|time for|now) (do )?"
                      r"(a |some )?(q and a|q a|questions)|q and a|open (it )?up for questions|"
                      r"(happy|love|glad) to (take|answer) (any |some )?questions|take (some |a few )?questions)\b")
# the speaker announcing the Q&A outright: clearly the Q&A wherever it falls ("I'm gonna take questions now")
ANNOUNCE = re.compile(r"\b(take questions now|take some questions now|open it up|open (it )?up for questions|"
                      r"let s do (a |some )?(q and a|q a|questions)|time for (some )?questions|move (on )?to (q and a|questions))\b")
# "hit me up if you got any questions", "use my QR code if you have questions": an invitation for later, not the Q&A
INVITE = re.compile(r"\b(hit me up|reach out|feel free to (reach|connect|ping|email|add)|connect with me|linkedin|find me|"
                    r"after the talk|later on|qr code|email me|ping me|grab me|come (find|talk to) me)\b")
# a greeting to the room: where a talk really starts, often before the name ("Hey, thanks for being here today ... I'm Jay")
# a greeting to the room: where a talk really starts, often before the name ("Hey, thanks for being here today ... I'm Jay").
# GREETING_STRONG is said to a room and nowhere else; the weaker openers ("so today", "my talk") need a pause before them
GREETING_STRONG = re.compile(r"\b(good (morning|afternoon|evening)|hello everyone|hello everybody|hi everyone|hi everybody|"
                             r"hey everyone|hey everybody|hey y all|hi y all|hi folks|hey folks|"
                             r"thanks for (being here|joining|having me|coming)|thank you for (being here|joining|having me|coming)|"
                             r"how is everybody|how s everybody|how is everyone|how s everyone|how are (you|y all) doing|"
                             r"nice to be here|great to be here|welcome everyone|right folks|all right folks|okay folks)\b")
GREETING = re.compile(GREETING_STRONG.pattern[:-3] + r"|hello|so today|today i m going to|today i want to|my talk|"
                      r"what i (want to|wanna) talk about|i m going to (talk|be talking) about|"
                      r"today we (are|re) going to|i will talk about)\b")
# a mic check / set-up line: a talk never starts on one ("can you hear me", "test", "is this on")
MICCHECK = re.compile(r"\b(can (you|y all|you all|everybody|everyone) hear me|hear me okay|is this on|check check|"
                      r"mic check|test test|^test|testing|do i need (to use )?the mic|is it recording|are we good)\b")
# ... and carrying on with the talk after a question in the middle: then it was not the Q&A
# the speaker saying the talk is over: "And that's our presentation", "That's it from me", "that's all I have"
END_MARK = re.compile(r"\b(that s (our|my) (presentation|talk)|that s it from me|that s all i have|that is all i have|"
                      r"that s everything i have|that is everything i have|that s all from me|that s my talk|"
                      r"thanks for your attention|thank you for your attention|thank you for your time|"
                      r"thanks for your time|and that s it|i ll end here|that s the end)\b")
# someone else thanking for the talk ("Thank you for the presentation"): the talk is over, the room has the floor
AUDIENCE_THANKS = re.compile(r"\b(thank you for the (presentation|talk)|thanks for the (presentation|talk)|"
                             r"great (talk|presentation)|thank you again for your talk)\b")
RESUME = re.compile(r"\b(moving on|move on|back to|let me continue|where was i|next slide|let s continue|carry on|"
                    r"so anyway|let s get back|going back to the|on the next)\b")
STOP = set("""a an and are as at be by for from how in into is it its of on or our the their this to we what when
where who why with without your you vs using use beyond building build make making about after also because been
being between both but can could does doing done during each every first from have just learn learned lessons like
more most much need needs other over really should some such than that them then there these they things those
through today under very want were which while will would years""".split())
# words every talk at these events uses: they say nothing about which talk this is
GENERIC = set("""reliability reliable engineering engineer engineers system systems production team teams platform
platforms service services cloud native approach session talk speaker share sharing world company companies scale
scaling modern practice practices journey future challenges challenge solution solutions workflow workflows tools
tooling infrastructure development developer developers software application applications operations""".split())


def stamp(sec):
    sec = int(round(sec))
    return "%d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def parse_stamp(s):
    """'1:02:03', '62:03', '3723' or 3723.5 -> seconds."""
    if isinstance(s, (int, float)):
        return float(s)
    parts = [float(x) for x in str(s).strip().split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _names(name):
    """The people in a 'name' cell: 'A & B', 'A, B and C', 'Adriana Villela - ClickHouse Meetup'."""
    name = re.split(r"\s+-\s+", name)[0]
    return [p.strip() for p in re.split(r",|&|\band\b", name) if p.strip() and not p.strip().startswith("_")]


COMMON = set("""also just really very going gonna here sure not now still actually currently originally happy glad
excited from with working work the a an so and but really kind sort""".split())


def _name_like(heard, name):
    """A heard word that is this name: the same (MJ), a nickname or a cut-off of it (Tim / Timothy), or a mishearing
    of the same length (Mailo / Mykhailo, Laya / Leila)."""
    if heard == name:
        return True
    if len(heard) < 3 or len(name) < 3:
        return False
    if name.startswith(heard) or heard.startswith(name):
        return True
    return abs(len(heard) - len(name)) <= 3 and difflib.SequenceMatcher(None, heard, name).ratio() >= 0.7


def _sounds_like(tw, target, n):
    """Whisper's spelling of a name ("Bob Martin as" for Bob Martinez): n or n+1 neighbouring words, run together,
    82% like the name run together. Only windows starting with the name's first letter are compared (fast)."""
    for k, w in enumerate(tw):
        if not w or w[0] != target[0]:
            continue
        for m in (n, n + 1):
            cand = "".join(tw[k:k + m])
            if abs(len(cand) - len(target)) <= 3 and difflib.SequenceMatcher(None, cand, target).ratio() >= 0.82:
                return True
    return False


# ---- signals --------------------------------------------------------------------------------------------------------
def hits(segments, lineup):
    """Per talk, where it shows up in the transcript: {key: [(seconds, kind, what)]}, kind is
       self   the speaker introduces themselves ("Hey, I'm Dan") - where a talk starts
       mc     the MC introduces them ("please welcome Dan Smith") - the talk starts right after it
       name   their name said some other way
       title  three or more of the title's words within a minute"""
    out = {}
    texts = [(s["start"], _norm(s["text"]), s["text"]) for s in segments]
    for item in lineup:
        if not item.get("key"):
            continue
        found = []
        for person in _names(item["name"]):
            words = _norm(person).split()
            if not words:
                continue
            full, first = " ".join(words), words[0]
            last = words[-1] if len(words[-1]) >= 4 and len(words) > 1 else None
            alts = [re.escape(full)] + ([re.escape(first)] if len(first) >= 2 else []) + ([re.escape(last)] if last else [])
            self_rx = re.compile(SELF % "|".join(alts))
            target = "".join(words)
            for at, text, raw in texts:
                padded = " %s " % text
                m = None if self_rx.search(text) else SELF_ANY.search(text)
                # Whisper / YouTube mishear names ("my name is Mailo" for Mykhailo, "Laya Anderson" for Leila):
                # a self-introduction also counts when the name that follows sounds like the speaker's
                heard = [w for w in (m.groups() if m else ()) if w and len(w) >= 2 and w not in STOP and w not in COMMON]
                if self_rx.search(text) or any(_name_like(h, n) for h in heard for n in words):
                    found.append((at, "self", "\"%s\"" % raw[:80]))
                elif " %s " % full in padded or (last and " %s " % last in padded) or (
                        len(target) >= 8 and _sounds_like(text.split(), target, len(words))):
                    found.append((at, "mc" if INTRO.search(raw) else "name", "\"%s\"" % raw[:80]))
        keys = [w for w in _norm(item["title"]).split() if len(w) >= 5 and w not in STOP and w not in GENERIC]
        if len(keys) >= 3:
            for i, (at, _, _) in enumerate(texts):          # 3+ title words within a minute of speech
                window = " ".join(t for a, t, _ in texts[i:i + 12] if a - at < 60).split()
                got = {k for k in keys if k in window}
                if len(got) >= 3 and not any(abs(at - f[0]) < 120 and f[1] == "title" for f in found):
                    found.append((at, "title", "title words: %s" % ", ".join(sorted(got))))
        out[item["key"]] = sorted(found)
    return out


def closings(segments):
    """Where someone wraps up: [(seconds, "qa" | "close", text)] - "any questions?" opens the Q&A, "thank you" /
    "that's all" usually ends a talk (or a Q&A answer)."""
    out = []
    for s in segments:
        t = _norm(s["text"])
        if QA.search(t):
            out.append((s["start"], "qa", s["text"][:80]))
        elif CLOSE.search(t):
            out.append((s["start"], "close", s["text"][:80]))
    return out


def _spoken_end(seg):
    """Where a line's words really stop: Whisper often stretches a segment's end over the silence after it, so a short
    line ("There we go.") is taken to last ~0.4 s a word (+1 s) at most."""
    return min(seg["end"], seg["start"] + 0.4 * len(seg["text"].split()) + 1)


def gaps(segments, duration, min_gap):
    """Pauses in speech of at least min_gap seconds, including before the first and after the last word."""
    out, last = [], 0.0
    for s in segments:
        if s["start"] - last >= min_gap:
            out.append((last, s["start"]))
        last = max(last, s["end"])
    if duration - last >= min_gap:
        out.append((last, duration))
    return out


def noise_floor(lv):
    """The room's noise level: the 20th percentile of the loudest channel per second."""
    v = sorted(max(l, r) for l, r in zip(lv["left"], lv["right"]))
    return v[len(v) // 5] if v else -90.0


def sound_threshold(lv):
    """A second counts as sound when it is 12 dB over the room's noise (Whisper's timestamps run on through the
    silence its voice detection removed, so where a talk really stops comes from the levels)."""
    return max(noise_floor(lv) + 12, -70.0)


def spoken(segs, a, b, length=None):
    """True when the transcript hears steady talking in [a, b] (a talk's pace is ~120-160 words a minute; 60+ counts):
    then a stretch the levels call quiet is room noise close to the speech level, not a drop-out (LLMday Austin
    2026 Q4: breaks at -36 dB, talks at -30 dB, so most of every afternoon talk looked "quiet")."""
    length = length or (b - a)
    words = sum(len(s["text"].split()) for s in segs if a <= s["start"] < b)
    return length >= 1 and words / (length / 60) >= 60


def quiet_spans(lv, min_len, thr=None, a=0, b=None):
    """Stretches of at least min_len seconds with no sound over the room noise, inside [a, b]: [(start, end)]."""
    thr = sound_threshold(lv) if thr is None else thr
    n = len(lv["left"])
    a, b = max(0, int(a)), min(n, int(b if b is not None else n))
    out, run_start = [], None
    for i in range(a, b):
        if max(lv["left"][i], lv["right"][i]) <= thr:
            run_start = i if run_start is None else run_start
        else:
            if run_start is not None and i - run_start >= min_len:
                out.append((float(run_start), float(i)))
            run_start = None
    if run_start is not None and b - run_start >= min_len:
        out.append((float(run_start), float(b)))
    return out


def tighten(lv, a, b, thr=None):
    """[a, b] shrunk to the first and last second with sound in it."""
    if not lv or not lv["left"]:
        return a, b
    thr = sound_threshold(lv) if thr is None else thr
    n = len(lv["left"])
    loud = lambda i: max(lv["left"][i], lv["right"][i]) > thr
    ia, ib = max(0, int(a)), min(n - 1, int(b))
    while ib > ia and not loud(ib):
        ib -= 1
    while ia < ib and not loud(ia):
        ia += 1
    return (float(ia), float(ib + 1)) if ib > ia else (a, b)


def track_recordings(state, track):
    """The recordings of one track in playing order (their clock if they have one, else the file name)."""
    recs = [(rid, r) for rid, r in state.get("recordings", {}).items() if str(r.get("track")) == str(track)]
    return sorted(recs, key=lambda x: (x[1].get("probe", {}).get("creation_time") or "~", x[1]["file"].lower()))


# ---- is it the right talk, and was it recorded properly ------------------------------------------------------------
def keywords(talk):
    """What a talk is about: {word: weight} - title words count 3, abstract words 1 (plurals folded)."""
    kw = {}
    for text, w in ((talk.get("abstract") or "", 1), (talk.get("title") or "", 3)):
        for word in _norm(text).split():
            word = word[:-1] if len(word) > 5 and word.endswith("s") else word
            if len(word) >= 5 and word not in STOP and word not in GENERIC and not word.isdigit():
                kw[word] = max(kw.get(word, 0), w)
    return kw


def content_scores(text, talks):
    """How well a stretch of transcript fits each talk's title + abstract: [(score, key)], best first. Score = the
    share of the talk's weighted keywords that were said, so short and long abstracts compare fairly."""
    said = set()
    for word in _norm(text).split():
        said.add(word[:-1] if len(word) > 5 and word.endswith("s") else word)
    out = []
    for t in talks:
        kw = keywords(t)
        total = sum(kw.values())
        if total >= 6:                                  # too few distinctive words to judge by
            out.append((round(sum(w for k, w in kw.items() if k in said) / total, 3), t["key"]))
    return sorted(out, reverse=True)


def qa_split(segs, start, end, max_qa=600):
    """Where the closing Q&A starts in a cut [start, end] - (seconds to end the cut at, what) - or (None, why it
    stays). Dropped only when it is clearly the Q&A: a "questions?" / "let's do Q&A" in the last 40% of the cut, at
    most max_qa seconds before its end (talks run 10-30 minutes; a longer stretch after it is the talk going on),
    and no "moving on" / "back to the slides" after it. A question in the middle of the talk keeps everything; when
    in doubt the Q&A stays. The cut ends after the speaker's own "thank you" right before it, or just before the
    question line."""
    talk = [s for s in segs if start <= s["start"] < end]
    span = end - start
    cands = [s for s in talk if QA_START.search(_norm(s["text"])) and not INVITE.search(_norm(s["text"]))]
    # "So that's my talk ... Thank you." with a minute or more of speech after it: an unannounced Q&A
    for i, s in enumerate(talk):
        t = _norm(s["text"])
        if (re.search(r"\b(thank you|thanks)\b", t) and len(t.split()) <= 6 and s["start"] >= start + span * 0.4
                and any(END_MARK.search(_norm(x["text"])) for x in talk[max(0, i - 12):i + 1] if s["start"] - x["start"] <= 60)
                and end - s["end"] >= 60):
            c = dict(s, unannounced=True)
            cands.append(c)
            break
    cands.sort(key=lambda s: s["start"])
    if not cands:
        return None, None
    why = "\"%s\" only mid-talk - kept" % cands[0]["text"][:40]
    for c in cands:
        ended = c.get("unannounced") or ANNOUNCE.search(_norm(c["text"])) or any(
            END_MARK.search(_norm(s["text"])) for s in talk if c["start"] - 45 <= s["start"] <= c["start"])
        if ended and c["start"] >= start + span * 0.3 and end - c["start"] <= max_qa * 2:
            pass                                         # "And that's our presentation. Any questions?": clearly the Q&A
        elif c["start"] < start + span * 0.6:
            continue                                     # a question in the middle: the talk goes on
        elif end - c["start"] > max(60, min(max_qa, span * 0.4)):
            why = "too much after \"%s\" for a Q&A - kept" % c["text"][:40]
            continue
        later = [s for s in talk if s["start"] > c["start"]]
        if not ended and any(RESUME.search(_norm(s["text"])) for s in later):
            return None, "the speaker carries on after \"%s\" - kept" % c["text"][:40]
        t = _norm(c["text"])
        m = QA_START.search(t)
        if c.get("unannounced") or CLOSE.search(t[:m.start()] if m else t):
            cut_at = c["end"]                            # "Thank you! Any questions?" in one line: keep the line
        else:
            cut_at = c["start"]
        if cut_at >= end - (2 if ended else 25):
            return None, None                            # the talk simply ends on it ("happy to answer any questions")
        return cut_at, "Q&A from %s dropped (%.1f min)" % (stamp(c["start"]), (end - cut_at) / 60)
    return None, why


def assess(run, cut, talks=None, planned=None, cache=None):
    """Flags (worth a look before approving) and notes (good to know) for one cut. talks: events.talks(); planned:
    {key: minutes on the schedule}; cache: {} shared between calls (recording data is read once)."""
    cache = {} if cache is None else cache
    talks = talks if talks is not None else events.talks(run.ev)
    if planned is None:
        planned = {x["key"]: x["minutes"] for v in events.schedule(run.ev).values() for x in v["items"] if x["key"]}
    flags, notes = [], []
    segs, loud, total, levels_seen, stills, blank, talked_through = [], [], 0.0, False, 0, 0, 0.0
    every, refs = [], []                                 # every second's level in the talk; each recording's talk level
    lv = None
    for p in cut.get("parts", []):
        rid, a, b = p["rec"], p["start"], p["end"]
        total += b - a
        if rid not in cache:
            folder = run.rec_dir(rid)
            cache[rid] = {"tj": analyse.load(folder, "transcript.json"), "lv": analyse.load(folder, "levels.json"),
                          "frames": os.path.join(folder, "frames")}
        data = cache[rid]
        if data["tj"]:
            segs += [s for s in data["tj"]["segments"] if s["start"] >= a - 1 and s["end"] <= b + 1]
        lv = data["lv"]
        if lv:
            levels_seen = True
            thr = sound_threshold(lv)
            secs = [max(l, r) for l, r in zip(lv["left"][int(a):int(b)], lv["right"][int(a):int(b)])]
            loud += [x for x in secs if x > thr]
            every += secs
            if "ref" not in data:                        # how loud this recording's talks usually are
                whole = sorted(max(l, r) for l, r in zip(lv["left"], lv["right"]))
                data["ref"] = whole[int(len(whole) * 0.75)] if whole else None
            if data["ref"] is not None:
                refs.append(data["ref"])
            inner_a, inner_b = a + (b - a) * 0.1, b - (b - a) * 0.1
            tsegs = (data["tj"] or {}).get("segments", [])
            for qa, qb in quiet_spans(lv, 30, thr, inner_a, inner_b):
                if spoken(tsegs, qa, qb):
                    talked_through += qb - qa         # the levels say quiet, the transcript hears a talk
                    continue
                flags.append("sound drops out for %d s at %s (%s)" % (qb - qa, stamp(qa), rid))
        if os.path.isdir(data["frames"]):
            for name in os.listdir(data["frames"]):
                m = re.match(r"^(\d+)\.jpg$", name)
                if m and a <= int(m.group(1)) <= b:
                    stills += 1
                    blank += os.path.getsize(os.path.join(data["frames"], name)) < 4000   # a plain frame packs tiny
    minutes = total / 60
    # recorded properly?
    if levels_seen:
        under = (max(refs) - statistics.median(every)) if refs and every else 0
        if under > 15 and (len(loud) < max(10, total * 0.2) or talked_through):
            # the transcript may still understand it (Whisper hears a mic left far away), but this talk sits far
            # under the recording's other talks - that is a bad recording, not room noise close to the speech
            flags.append("much quieter than the rest of the recording (%.0f dB under) - mic far away or off?" % under)
        elif len(loud) < max(10, total * 0.2) and spoken(segs, 0, 1e9, total):
            notes.append("the levels can't tell this talk from the room noise (speech only a few dB over the breaks), "
                         "but the transcript hears it all the way through - sound is there")
        elif len(loud) < max(10, total * 0.2):
            flags.append("hardly any sound (%d of %d s) - mic off or not recorded?" % (len(loud), total))
        else:
            med = statistics.median(loud)
            if med < -42:
                flags.append("very quiet sound (median %.0f dBFS) - mic gain low or far from the speaker" % med)
            hot = sum(1 for x in loud if x > -4) / len(loud)
            if hot > 0.02:
                flags.append("sound distorts / clips in %.0f%% of the talk" % (hot * 100))
    if talked_through and not any(f.startswith("much quieter") for f in flags):
        notes.append("%d s the levels call quiet have speech in the transcript - room noise close to the speech, "
                     "not drop-outs" % talked_through)
    if segs:
        words = sum(len(s["text"].split()) for s in segs)
        wpm = words / max(minutes, 0.1)
        if minutes >= 3 and wpm < 60:
            flags.append("few words heard (%.0f a minute; a talk has ~120-160) - bad sound or long silences" % wpm)
        lps = [s.get("lp") for s in segs if s.get("lp") is not None]
        if lps and statistics.mean(lps) < -0.9:
            flags.append("the transcript is unsure (avg log-prob %.2f) - muffled, echoing or noisy sound" % statistics.mean(lps))
    elif minutes >= 2:
        flags.append("no words heard in this cut - not recorded, or not transcribed yet?")
    if stills >= 3 and blank / stills > 0.5:
        flags.append("the picture is blank in %d of %d stills - camera off or no signal?" % (blank, stills))
    plan = planned.get(cut.get("key"))
    kind = next((t["kind"] for t in talks if t["key"] == cut.get("key")), "talk")
    lo, hi = float(run.cfg.get("talk_min_minutes", 10)), float(run.cfg.get("talk_max_minutes", 30))
    if kind != "workshop" and minutes < lo * 0.8:
        flags.append("only %.1f minutes - talks run %g-%g; part of it missing, or cut too short?" % (minutes, lo, hi))
    if kind != "workshop" and minutes > hi * 1.35:
        flags.append("%.0f minutes - talks run %g-%g; two talks in one cut, or a long Q&A?" % (minutes, lo, hi))
    if plan and plan >= 10 and minutes < plan * 0.5:
        flags.append("only %.0f of %d planned minutes - recording incomplete, or cut too short?" % (minutes, plan))
    if plan and plan >= 10 and minutes > plan * 1.7 + 5:
        flags.append("%.0f minutes for a %d-minute slot - two talks in one cut?" % (minutes, plan))
    # the right talk?
    if segs:
        text = " ".join(s["text"] for s in segs)
        scores = content_scores(text, talks)
        own = next((sc for sc, k in scores if k == cut.get("key")), None)
        if scores and own is not None:
            best, best_key = scores[0]
            if best_key != cut.get("key") and best >= 0.15 and best >= own * 1.5:
                flags.append("sounds like %s's talk (topic words %.0f%% vs %.0f%% for this one) - swapped slot?"
                             % (best_key, best * 100, own * 100))
            else:
                notes.append("topic words match: %.0f%%" % (own * 100))
        # the speaker's own opening, and a closing line near the end
        talk = next((t for t in talks if t["key"] == cut.get("key")), None)
        if talk:
            first_min = [s for s in segs if s["start"] < segs[0]["start"] + 90]
            if any(h[1] == "self" for h in hits(first_min, [talk]).get(talk["key"], [])):
                notes.append("starts with the speaker introducing themselves")
            elif minutes >= 3:
                notes.append("no \"I'm <name>\" at the start - check the first seconds")
            tail_from = segs[0]["start"] + (segs[-1]["end"] - segs[0]["start"]) * 0.7
            kinds = {k for t, k, _ in closings([s for s in segs if s["start"] >= tail_from])}
            last = cut["parts"][-1]
            qa_at, qa_why = qa_split(segs, segs[0]["start"], last["end"], float(run.cfg.get("max_qa_minutes", 10)) * 60)
            if qa_at:
                notes.append("a Q&A is still in the cut from %s - end it at %s to drop it" % (stamp(qa_at), stamp(qa_at)))
            elif qa_why:
                notes.append(qa_why)
            else:
                asked = [sg for sg in segs if sg["start"] >= tail_from and ASKED.search(_norm(sg["text"]))]
                if asked:
                    notes.append("someone asks a question at %s (\"%s\") - an unannounced Q&A? Left in; end the cut "
                                 "before it if it clearly is" % (stamp(asked[0]["start"]), asked[0]["text"][:40]))
            if "qa" in kinds or "close" in kinds:
                notes.append("ends after %s" % ("questions" if "qa" in kinds else "a closing \"thank you\""))
            elif minutes >= 3:
                notes.append("no closing \"thank you\" or questions near the end - check the end")
    return {"flags": flags, "notes": notes}


def annotate(run, data):
    """assess() every cut of a cuts.json (in place) and name the lineup talks that are neither cut nor skipped."""
    talks = events.talks(run.ev)
    sch = events.schedule(run.ev)
    planned = {x["key"]: x["minutes"] for v in sch.values() for x in v["items"] if x["key"]}
    cache = {}
    for c in data.get("cuts", []):
        c.update(assess(run, c, talks, planned, cache))
    recorded = {str(r.get("track")) for r in run.state.get("recordings", {}).values()}
    have = {c["key"] for c in data.get("cuts", [])} | {s["key"] for s in data.get("skipped", [])}
    data["missing"] = [x["key"] for tr in sorted(recorded & set(sch)) for x in sch[tr]["items"]
                       if x["key"] and x["key"] not in have]
    return data


# ---- the brief for Claude ---------------------------------------------------------------------------------------------
def brief(run):
    """Write segment_brief_<track>.md per track that has analysed recordings; returns the paths."""
    sch, paths = events.schedule(run.ev), []
    talks = {t["key"]: t for t in events.talks(run.ev)}
    tracks = sorted({str(r.get("track")) for r in run.state.get("recordings", {}).values()})
    min_gap = float(run.cfg.get("min_gap_seconds", 20))
    for tr in tracks:
        if tr not in sch:
            continue
        info = sch[tr]
        L = ["# %s - track %s (%s, day %d, %s)" % (run.ev["title"], tr, info["room"], info["day"], info["date"]), "",
             "Write the cuts into %s (format: live_factory/segment.py), then check them with "
             "`python -m live_factory cuts %s`." % (run.cuts_path, run.ev["id"]), "",
             "How to cut - a wrong cut is worse than none:",
             "- A talk starts at the speaker's first words - usually \"Hey, I'm Dan\" (see *Speakers introducing "
             "themselves*), not at the MC's introduction - and ends at their closing \"thank you\", before the MC's "
             "thanks and the next set-up. Talks run 10-30 minutes, workshops longer.",
             "- Cut off the closing Q&A (\"any questions?\", \"let's do Q&A\", then someone asking from the back "
             "without a microphone) when it clearly is the Q&A. A question in the middle of the talk, after which the "
             "speaker carries on, is part of the talk. When in doubt, leave the Q&A in.",
             "- What published talks look like (SREday / LLMday channels, 2026): they open on \"Okay, my name is "
             "Leila Anderson ...\", \"I'm Stephen, I'm a product manager at ...\", \"Hello everyone ... My name is ...\" - "
             "names are often misheard (Mailo = Mykhailo). Some speakers open with mic checks and set-up chat (\"You can "
             "hear me?\"): start at the first words to the room once that is over. They end on \"So I think that is my "
             "time ... Thank you all very much\", or simply on their last point. Questions during a demo (\"while it's "
             "running, any questions?\") belong to the talk. Talks run 10-30 minutes (median ~20), keynotes 13-27.",
             "- Confirm every talk by what is said: the name, and the title and abstract below against the transcript. "
             "Live days run late, swap slots and lose talks.",
             "- Look at frames around each boundary: `python -m live_factory frame %s <rec> <h:mm:ss>` (the "
             "speaker on stage, their title slide)." % run.ev["id"],
             "- Not sure? Give the cut `confidence: low` and say why in `reason` - or put the talk in `skipped` with "
             "`why` (\"not recorded\", \"sound unusable\", \"no-show\"). Never guess a cut into place.",
             "- A talk that runs from one recording into the next gets two parts.", "",
             "## Running order (the plan)", ""]
        for x in info["items"]:
            if not x["key"]:
                L.append("- %s-%s  _%s_" % (x["start"], x["end"], x["title"]))
                continue
            t = talks.get(x["key"], {})
            abstract = re.sub(r"\s+", " ", t.get("abstract") or "").strip()
            L.append("- %s-%s  **%s** (%s%s) - %s%s" % (
                x["start"], x["end"], x["key"], t.get("organization") or "?", ", " + x["kind"] if x["kind"] != "talk" else "",
                x["title"], ("\n  > " + abstract[:400] + ("..." if len(abstract) > 400 else "")) if abstract else ""))
        lineup = [x for x in info["items"] if x["key"]]
        for rid, r in track_recordings(run.state, tr):
            folder = run.rec_dir(rid)
            tr_json = analyse.load(folder, "transcript.json")
            dur = r.get("probe", {}).get("duration") or 0
            L += ["", "## Recording %s: %s" % (rid, r["file"]), "",
                  "- length %s, recorder clock at the start: %s" % (stamp(dur), r.get("probe", {}).get("creation_time") or "unknown"),
                  "- sound: %s" % ((r["steps"].get("levels") or {}).get("note") or "?"),
                  "- stills every %s s: %s" % (run.cfg.get("frame_every_seconds") or 120, os.path.join(folder, "frames"))]
            if not tr_json:
                L.append("- NOT TRANSCRIBED YET - run `python -m live_factory analyse %s`" % run.ev["id"])
                continue
            segs = tr_json["segments"]
            lv = analyse.load(folder, "levels.json")
            found = hits(segs, lineup)
            for title, kinds in (("Speakers introducing themselves (where talks start)", ("self",)),
                                 ("MC introductions (the talk starts right after)", ("mc",)),
                                 ("Other mentions of the names, and title words", ("name", "title"))):
                L += ["", "### " + title, ""]
                rows = ["- %s **%s**: %s" % (stamp(a), key, w) for key, hs in found.items() for a, k, w in hs if k in kinds]
                L += sorted(rows, key=lambda r: parse_stamp(r[2:].split(" ")[0])) or ["- none"]
            L += ["", "### Closing lines (\"thank you\", \"any questions\")", ""]
            L += ["- %s %s: %s" % (stamp(a), "Q&A" if k == "qa" else "close", t) for a, k, t in closings(segs)] or ["- none"]
            L += ["", "### Pauses of %d s or more" % min_gap, "",
                  "quiet = no sound over the room noise (exact, from the levels); no speech = from the transcript "
                  "(Whisper's segment ends run on into the quiet).", ""]
            spans = sorted([(a, b, "quiet") for a, b in (quiet_spans(lv, min_gap) if lv else [])] +
                           [(a, b, "no speech") for a, b in gaps(segs, dur, min_gap)])
            L += ["- %s - %s (%d s, %s)" % (stamp(a), stamp(b), b - a, w) for a, b, w in spans] or ["- none"]
            L += ["", "### Transcript", ""]
            L += ["[%s] %s" % (stamp(s["start"]), s["text"]) for s in segs]
        path = os.path.join(run.dir, "segment_brief_%s.md" % re.sub(r"[^\w-]+", "_", tr))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
        paths.append(path)
    return paths


# ---- the automatic first guess ----------------------------------------------------------------------------------------
def draft(run):
    """A first guess at the cuts of every analysed track. The running order is walked with its slippage carried
    forward (each talk is expected where the previous one really started plus its slot, plus a break when the plan
    has one); a talk's start is, best first: the speaker introducing themselves, the line after the MC's
    introduction, another mention of the name, or speech resuming after a pause near the expected time. It ends at
    the quiet before the next introduction (or a break's long pause), tightened to the real sound."""
    sch, out, skipped = events.schedule(run.ev), [], []
    cfg = run.cfg
    min_gap = float(cfg.get("min_gap_seconds", 20))
    lead, tail = float(cfg.get("cut_lead_seconds", 2)), float(cfg.get("cut_tail_seconds", 3))
    tracks = sorted({str(r.get("track")) for r in run.state.get("recordings", {}).values()})
    for tr in tracks:
        if tr not in sch:
            continue
        items, done = sch[tr]["items"], set()        # a talk found in one piece of the day is not looked for again
        for rid, r in track_recordings(run.state, tr):
            tj = analyse.load(run.rec_dir(rid), "transcript.json")
            if not tj or not tj["segments"]:
                continue
            segs, dur = tj["segments"], r.get("probe", {}).get("duration") or tj["segments"][-1]["end"]
            found = hits(segs, [x for x in items if x["key"]])
            anchor, first_i = None, 0                # the first talk heard by name anchors the walk
            for i, x in enumerate(items):
                if x["key"] and x["key"] not in done and found.get(x["key"]):
                    anchor, first_i = found[x["key"]][0][0], i
                    break
            if anchor is None:
                first_i = next((i for i, x in enumerate(items) if x["key"] and x["key"] not in done), len(items))
                anchor = segs[0]["start"]
            lv = analyse.load(run.rec_dir(rid), "levels.json")
            quiet = quiet_spans(lv, max(min_gap, 60)) if lv else []
            at_seg = {sg["start"]: i for i, sg in enumerate(segs)}
            expected, placed, held = anchor, [], []
            for x in items[first_i:]:
                if x["key"] in done:
                    continue
                if not x["key"]:
                    expected += x["minutes"] * 60
                    continue
                after = placed[-1]["start"] + 30 if placed else 0
                window = [h for h in found.get(x["key"], []) if h[0] > after and (
                          abs(h[0] - expected) < 20 * 60 if h[1] in ("self", "mc") else abs(h[0] - expected) < 15 * 60)
                          and h[1] in ("self", "mc", "name")]
                if not window:
                    # a later talk's speaker clearly introducing themselves around here: this one swapped slots or
                    # did not happen - look for it again after that talk, don't guess it into this place
                    later = [y for y in items[items.index(x) + 1:] if y["key"] and y["key"] not in done
                             and any(h[1] in ("self", "mc") and h[0] > after and abs(h[0] - expected) < 10 * 60
                                     for h in found.get(y["key"], []))]
                    if later:
                        held.append(x)
                        continue
                if not window and expected > dur - 10:      # past the end, and nobody says the name: not in here
                    break
                rank = {"self": 0, "mc": 1, "name": 2}
                window.sort(key=lambda h: (rank[h[1]], h[0]))
                mc = next((h for h in window if h[1] == "mc"), None)
                own = next((h for h in window if h[1] == "self" and (not mc or 0 <= h[0] - mc[0] < 180)), None)
                if own:                                      # "Hey, I'm Dan": the talk starts here - or at the greeting
                    start = intro = own[0]                   # before it ("Hey, thanks for being here ... I'm Jay")
                    if mc and mc[0] < own[0]:
                        intro = mc[0]
                    i = at_seg.get(own[0])
                    if i is not None:
                        k, greet = i, None
                        while k > 0 and own[0] - segs[k - 1]["start"] <= 90:
                            prev = segs[k - 1]
                            mc_line = INTRO.search(prev["text"]) and not re.search(r"introduce (myself|ourselves)", prev["text"], re.I)
                            if segs[k]["start"] - prev["end"] > 15 or MICCHECK.search(_norm(prev["text"])) or mc_line:
                                break                            # a long pause, a mic check or the MC: before the talk
                            k -= 1
                            if GREETING.search(_norm(segs[k]["text"])):
                                greet = k
                        if greet is not None and segs[greet]["start"] < own[0]:
                            start = segs[greet]["start"]
                            intro = min(intro, start)
                    how, conf = "speaker introduces themselves at %s" % stamp(own[0]), "high"
                    if start < own[0]:
                        how += " (greets the room from %s)" % stamp(start)
                elif mc:                                     # the MC's introduction: the talk starts on the next line
                    intro, i = mc[0], at_seg.get(mc[0])
                    start = segs[i + 1]["start"] if i is not None and i + 1 < len(segs) else mc[0]
                    how, conf = "MC introduces them at %s" % stamp(mc[0]), "high" if abs(mc[0] - expected) < 6 * 60 else "medium"
                elif window:
                    start = intro = window[0][0]
                    how, conf = "name heard at %s" % stamp(start), "medium"
                else:
                    # no name: a greeting to the room ("how's everybody doing?") after a pause or a mic check
                    greets = [sg for k, sg in enumerate(segs) if abs(sg["start"] - expected) < 8 * 60 and sg["start"] > after
                              and GREETING.search(_norm(sg["text"])) and k and (
                                  GREETING_STRONG.search(_norm(sg["text"])) or sg["start"] - _spoken_end(segs[k - 1]) >= 4
                                  or any(MICCHECK.search(_norm(z["text"])) for z in segs[max(0, k - 12):k]
                                         if sg["start"] - z["start"] <= 30))]
                    near = [b for a, b in gaps(segs, dur, 8) if abs(b - expected) < 8 * 60 and b > after]
                    if greets:
                        start = min(greets, key=lambda g: abs(g["start"] - expected))["start"]
                        how, conf = "greets the room at %s - no name heard" % stamp(start), "medium"
                    elif near:
                        start = min(near, key=lambda b: abs(b - expected))
                        how, conf = "speech resumes after a pause near the planned time - no name heard", "low"
                    else:
                        start = expected
                        how, conf = "planned time only - no name heard", "low"
                    intro = start
                if start > dur - 30:                         # the recording ends before this talk
                    break
                # the lead-in never reaches back into the words before (the MC's "... please welcome Dan")
                floor = max([sg["end"] for sg in segs if sg["end"] <= start + 0.05] or [0.0])
                placed.append({"key": x["key"], "name": x["name"], "start": max(0.0, floor, start - lead), "floor": floor,
                               "intro": intro,
                               "minutes": x["minutes"], "how": how, "confidence": conf})
                expected = start + x["minutes"] * 60
            for i, p in enumerate(placed):
                limit = placed[i + 1]["intro"] if i + 1 < len(placed) else dur
                last = [sg["end"] for sg in segs if sg["start"] >= p["start"] and sg["start"] < limit - 0.5]
                pauses = [a for a, b in gaps(segs, dur, max(min_gap, 60)) + quiet if p["start"] + p["minutes"] * 30 < a < limit]
                # a quiet stretch shortly before the next introduction: the talk ended there, the MC talked after it
                handover = [a for a, b in (quiet_spans(lv, min_gap) if lv else []) if p["start"] < a and limit - b < 90
                            and b <= limit] if i + 1 < len(placed) else []
                breaks = [a for a in pauses if any(b - a2 >= 180 for a2, b in quiet if a2 == a)]
                end = (min(handover + breaks) if handover or breaks else min(pauses) if pauses
                       else (max(last) if last else limit))
                # someone thanking the speaker by name ("Thank you, David!") is the MC: the talk ended before that
                names = {w for person in _names(p["name"]) for w in _norm(person).split() if len(w) >= 3}
                thanked = [sg["start"] for sg in segs if p["start"] + 20 < sg["start"] < limit
                           and (CLOSE.search(_norm(sg["text"])) and names & set(_norm(sg["text"]).split())
                                or AUDIENCE_THANKS.search(_norm(sg["text"])) and sg["start"] > p["start"] + 300)]
                hard = min([limit] + thanked)           # the next voice that is not this talk's
                if thanked:
                    end = min(end, hard)
                # tightened to the real sound, 1.5 s clear of the next voice (a level second straddles it)
                a, b = tighten(lv, p["start"] + lead, min(end, hard - 1.5)) if lv else (p["start"] + lead, min(end, hard))
                cut_end = min(dur, b + tail, hard - 0.5)
                how = p["how"]
                # a few stray words after the final "thank you" ("Yep." ... "Wait.") are not the talk
                inside = [sg for sg in segs if a <= sg["start"] < cut_end]
                closes = [i for i, sg in enumerate(inside) if CLOSE.search(_norm(sg["text"]))
                          and sg["start"] > a + (cut_end - a) * 0.8]
                if closes:
                    after = inside[closes[-1] + 1:]
                    if after and sum(len(sg["text"].split()) for sg in after) <= 6:
                        cut_end = min(cut_end, inside[closes[-1]]["end"] + tail, after[0]["start"] - 0.2)
                        how += "; stray words after the closing line dropped"
                if cfg.get("cut_qa", True):              # the closing Q&A goes - but only when it clearly is one
                    qa_at, qa_why = qa_split(segs, a, cut_end, float(cfg.get("max_qa_minutes", 10)) * 60)
                    if qa_at:
                        a2, b2 = tighten(lv, a, qa_at - 0.8) if lv else (a, qa_at)
                        nxt = min([sg["start"] for sg in segs if sg["start"] >= qa_at - 0.05] or [qa_at + tail])
                        cut_end = min(cut_end, b2 + tail, nxt - 0.2)
                    if qa_why:
                        how += "; " + qa_why
                a = p["start"] + lead                    # the start stays on the transcript: a quiet first line (mic not
                                                         # live yet) is still the talk; only the end follows the levels
                out.append({"key": p["key"], "parts": [{"rec": rid, "start": round(max(0.0, p["floor"], a - lead), 1),
                                                         "end": round(cut_end, 1)}],
                            "confidence": p["confidence"], "reason": how, "by": "draft", "approved": False})
                done.add(p["key"])
    have = {c["key"] for c in out}
    for tr in set(tracks) & set(sch):
        for x in sch[tr]["items"]:
            if x["key"] and x["key"] not in have:
                skipped.append({"key": x["key"], "why": "not found in the recordings - not recorded properly, or a "
                                                        "no-show? (draft)"})
    return annotate(run, {"cuts": out, "skipped": skipped})


# ---- cuts.json -----------------------------------------------------------------------------------------------------------
def validate(run, data):
    """Problems with a cuts.json, as readable lines (empty = fine). Hard errors start with 'ERROR'."""
    keys = {t["key"] for t in events.talks(run.ev)}
    recs = run.state.get("recordings", {})
    probs, seen, spans = [], {}, {}
    for c in data.get("cuts", []):
        k = c.get("key")
        if k not in keys:
            probs.append("ERROR %r is not a talk of %s (keys are the speaker names as in talks.csv)" % (k, run.ev["id"]))
        if k in seen:
            probs.append("ERROR %s has two cuts" % k)
        seen[k] = c
        if not c.get("parts"):
            probs.append("ERROR %s has no parts" % k)
        for p in c.get("parts", []):
            r = recs.get(p.get("rec"))
            if not r:
                probs.append("ERROR %s: no recording %r" % (k, p.get("rec")))
                continue
            a, b = parse_stamp(p.get("start", 0)), parse_stamp(p.get("end", 0))
            dur = r.get("probe", {}).get("duration") or 0
            if not 0 <= a < b or (dur and b > dur + 1):
                probs.append("ERROR %s: %s-%s is not inside %s (0-%s)" % (k, stamp(a), stamp(b), p["rec"], stamp(dur)))
            spans.setdefault(p["rec"], []).append((a, b, k))
    for s in data.get("skipped", []):
        if s.get("key") in seen:
            probs.append("ERROR %s is both cut and skipped" % s.get("key"))
    for rec, s in spans.items():
        s.sort()
        for (a1, b1, k1), (a2, b2, k2) in zip(s, s[1:]):
            if a2 < b1:
                probs.append("ERROR %s and %s overlap in %s (%s-%s)" % (k1, k2, rec, stamp(a2), stamp(b1)))
    return probs


def normalise(data):
    """Times as seconds (h:mm:ss accepted on the way in), defaults filled."""
    for c in data.get("cuts", []):
        c.setdefault("confidence", "medium")
        c.setdefault("reason", "")
        c.setdefault("by", "claude")
        c.setdefault("approved", False)
        for p in c.get("parts", []):
            p["start"], p["end"] = round(parse_stamp(p["start"]), 2), round(parse_stamp(p["end"]), 2)
    data.setdefault("skipped", [])
    return data


def load(path):
    if not os.path.exists(path):
        return {"cuts": [], "skipped": []}
    with open(path, encoding="utf-8") as f:
        return normalise(json.load(f))


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(normalise(data), f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
