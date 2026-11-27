"""Sharp starts and ends: every talk starts on the speaker's first words to the room and ends right after their last
sentence; the Q&A is always out, and a talk never stops mid-sentence.

Marek, 2026-11-27: "Q&A need to always be outed as they don't prove consistency (some questions are bad, mics are not
passed in time, etc.) - so we want to end sharply at the moment when the person finishes, but letting him finish the
sentence. So strip the last sentence if the sense allows it ... I just don't want someone to finish mid sentence."

The cut's coarse edges (cuts.json, from the whole-day transcript) are listened to again, close up: Whisper large-v3
with word timestamps and punctuation on a window around each edge (listen()). Then:

  start  the speaker's first sentence to the room - a greeting, their name, "thanks for having me" - after the MC's
         introduction, mic checks ("can you hear me?") and slide set-up ("let me share my screen"); LEAD s before
         its first word, in the quietest moment there.
  end    where the talk proper is over (the Q&A opening, a question from the room, the MC taking the floor back), then
         back to the last sentence that may end a talk: finished (. ! ?), a pause after it, not hanging on "and" /
         "so" / "the", not a question to the room - "any questions?", "so yeah", "and with that" are stripped; the
         closing "thank you" is the ideal last line. TAIL s after its last word, in the quietest moment there.

Anything unsure is flagged, never guessed: a flagged talk stops before Descript until Claude (ends_brief.md) or Marek
confirms its edges in cuts.json ("ends": {"start": .., "end": .., "by": "claude", "why": ".."}).
"""
import json
import os
import re
import subprocess

import numpy as np

from . import analyse, media, segment

RATE = 16000
LEAD = 0.4                    # seconds before the first word
TAIL = 0.8                    # ... after the last one
GAP_AFTER = 0.2               # a sentence may end a talk only with at least this much quiet after it (fluent
#                               speakers leave 0.1-0.3 s between sentences; a finished sentence is what counts)
BACKTRACK = 60.0              # never step back further than this from where the talk proper is over
MAX_STEPS = 3                 # ... nor more than this many sentences
START_BEFORE, START_AFTER = 75.0, 90.0     # the window listened to around the coarse start
END_BEFORE, END_AFTER = 180.0, 60.0        # ... and around the coarse end (or the Q&A found in it)

# a sentence never ends a talk on these
DANGLING = set("""and so but because the to of or if which with a an for in on at is are was were we i my our
like as than then when where while who whose your their its into from by about very um uh any""".split())
# ("this" / "that" / "these" can end a sentence: "... other patterns very similar to this.")
# the MC handing over / taking the floor back
MC = re.compile(r"\b(please welcome|welcome (him|her|them)|give it up|round of applause|our next speaker|next speaker|"
                r"next up|up next|without further ado|welcome to the stage|on stage|let s welcome|let s hear|"
                r"over to you|floor is yours|stage is yours|take it away|i ll hand (it )?over|introduce (our|the)|"
                r"thank you so much for (the|that|your) (talk|presentation|session)|let s thank|"
                r"another round|big round|thanks again to|(turn|turning|hand|handing|pass|passing) it (over )?to|"
                r"over to my (good )?friend|coming up next|without further)\b")
# slide set-up and the laptop: "let me share my screen", "one second", "can you see my slides?"
SETUP = re.compile(r"\b(let me (share|get|pull|bring|switch|put|open|find|plug|mirror)|one (sec|second|moment)|"
                   r"give me (a|one) (sec|second)|can (you|everyone|everybody) see (my|the|this) (screen|slides)|"
                   r"(is|are) (my|the) (screen|slides|mic|microphone)|clicker|full screen|presenter (mode|view)|"
                   r"am i (on|good|live)|are we (good|live)|is it working|is this working|sorry about that|"
                   r"bear with me|how do i (go|switch|move)|which (button|one is))\b")
# the speaker opening the Q&A, in more words than segment.QA_START: "I'll stop there and answer any kind of
# questions", "I'll wrap it up here and start with questions", "open the floor"
QA_OPEN = re.compile(r"\b((answer|take|field) (any|some|a few|your|all)? ?(kind of |of the |other )?questions|"
                     r"start with (the )?questions|open (it|the floor|things) (up )?(for|to) questions|open the floor|"
                     r"(stop|wrap( it)? up|end|pause) (here|there) and (take|answer|start|open|do)|"
                     r"^(but |and |so |okay |ok )?(any )?(other )?questions$|\b(but|any|so|okay) (uh |um |er )?(any )?(other )?questions\b|"
                     r"(minutes|minute|time) (left )?for (some |a few |any )?questions|(questions|q and a|q a) (now|time)|(we|i) (have|got) (some )?time for (a few |some )?questions)\b")
# "That concludes (my talk), any questions?" - where Whisper runs it into the last line
CONCLUDES = re.compile(r"\b(that|this|which|and that) conclude(s|d)?\b")
# the room has the floor: someone asks, or thanks the speaker
ROOM = re.compile(r"\b(great question|good question|thanks for the question|to repeat the question|"
                  r"thank you for the (presentation|talk)|thanks for the (presentation|talk)|great (talk|presentation)|"
                  r"i have a question|my question is|quick question|can you (talk|speak) (a bit )?(more )?about)\b")
# stripped from the end of a talk when the sense allows (they lead into the Q&A or are filler)
STRIP = re.compile(r"^(so )?(yeah|okay|ok|cool|all right|alright|right|great|awesome|and|so|um|uh|"
                   r"and with that|so with that|with that)( yeah)?$")          # "That's it." is a closing: kept
THANKS = re.compile(r"\b(thank you|thanks)\b")
# "are we good to go?", "I think we can start", "let me just adjust this", "first one minute"
READY = re.compile(r"\b((i think|i guess|i assume|i believe) (we|i) (can|could|should) (start|begin|get started|go)|"
                   r"(can|shall|should) (we|i) (start|begin|get started)|(are|is) (we|everyone|everybody) (good|ready|set)|"
                   r"we re (good|ready|set)|good to go|(start|begin) (now|right now)|would like to start|"
                   r"let me (just )?(adjust|fix|move|set|lower|raise|grab|turn)|first one minute|"
                   r"(i hope|hopefully) (you|everyone|everybody|you all) can hear me|can everyone hear|"
                   r"(is|are) (it|this|that|the mic|the microphone) (loud|ok|okay|on|fine)|i m ready|we re ready|"
                   r"(gonna|going to) start|start soon|let s get going|turn on (my|the) (mic|microphone)|"
                   r"(someone|somebody|anyone) connected|connected to the)\b")
# the room and the tech desk: "hang on", "look at the power button", "yeah I hear you", "there you are"
SETUP2 = re.compile(r"\b(hang on|hold on|power button|there you go|oh okay|need to change|share (my |your |the )?screen|on the zoom|toss it|in your pocket|sound check|i (can )?hear you|we (can )?hear you|there you are|you re muted|"
                    r"unmute|muted or not|one more minute|can you guys hear|can you hear|hear me now|is it working|"
                    r"which (one|button)|press (the|this)|plug(ged)? in|hdmi|adapter|clicker|my slides)\b")
# a greeting to the room, more than segment.GREETING_STRONG knows ("all right everyone, thanks for being out here")
HELLO = re.compile(r"\b(all right everyone|alright everyone|all right everybody|alright everybody|okay everyone|ok everyone|"
                   r"okay everybody|hi everyone|hello everyone|hey everyone|good (morning|afternoon|evening)|"
                   r"thanks? (you )?for (being (out )?here|coming( out)?|having (me|us)|inviting (me|us)|joining)|"
                   r"thank you (so|very) much for (inviting|having)|welcome everyone|welcome everybody|"
                   r"thank(s| you)( so much| very much)?( everyone| everybody| all| guys| folks| again)?,? ?(so much )?for "
                   r"(being|coming|joining|having|inviting|staying|showing))\b")
# where a talk proper starts inside a run of chatter
START_MARK = re.compile(r"\b(well first off|first off|first of all|so first|so today|today (i|we)|my name|"
                        r"let s (get started|start|begin|dive)|hello|hi everyone|hey everyone|good (morning|afternoon|evening))\b")
# a bare word to the room before the talk: "Okay." "All right." "Thank you." (to the MC / the applause)
FILLER = re.compile(r"^(ok|okay|all right|alright|right|so|good|cool|great|perfect|yeah|yes|um|uh|wow|oh|"
                    r"(ok |okay |all right |alright |great |perfect |well )?(thank you|thanks)( (so|very) much)?( all)?"
                    r"( (thank you|thanks))*|"
                    r"(ok|okay|all right|alright) (so|then|cool|great|perfect))$")
# a talk that opens with these, with no pause before them, is caught mid-sentence
JOINING = set("and but because which that or then also whereas plus".split())   # "So ..." and "Who here ...?" are real openings
# Whisper's echo in silence and applause ("Thank you for watching." over and over)
ECHO = re.compile(r"\b(thank you for watching|thanks for watching|see you in the next (video|one)|please subscribe|"
                  r"subtitles by|transcribed by)\b")
OPENER = re.compile(segment.GREETING.pattern[:-3] + r"|hi|hey|thank you|thanks|my name|i m|so today|alright so|welcome|hello all|hi all|"
                    r"okay so|all right so|let s (get started|begin|start|dive)|before i (start|begin))\b")


# No prompt (SREday Dallas: a punctuated prompt came back as words in the quiet - "So, today I'm going to talk about
# reliability.") and no carried context (it loops through a quiet minute: one sentence nine times). A stretch with no
# punctuation is not guessed at: its sentences don't count as finished, and the edge is flagged for Claude.
PROMPT = "v6"
# As the whole-day transcript (analyse.py), which heard every greeting on SREday Dallas: the voice detector with its
# default 2 s of silence, no carried context - plus word times and the careful search (beam 5). A 400 ms detector
# chopped the speech into scraps: quieter greetings dropped, punctuation lost; carried context looped through quiet
# minutes; a prompt came back as words; hallucination_silence_threshold dropped a real greeting.
WHISPER = dict(beam_size=5, word_timestamps=True, condition_on_previous_text=False, vad_filter=True)
# (not hallucination_silence_threshold: on Girish Konda's start it dropped his real "Hey, good afternoon, I'm Girish"
# and joined the MC's "So, Girish." to his next sentence - the times matter more than anything here)


# ---- listening --------------------------------------------------------------------------------------------------------
def pcm(src, a, b):
    raw = subprocess.run([media.tool("ffmpeg"), "-v", "error", "-ss", "%.3f" % max(0.0, a), "-t", "%.3f" % (b - max(0.0, a)),
                          "-i", src, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def envelope(x, step=0.05):
    n = int(RATE * step)
    k = len(x) // n
    if not k:
        return np.zeros(0)
    return 20 * np.log10(np.sqrt((x[:k * n].reshape(k, n).astype(np.float64) ** 2).mean(1)) + 1e-7)


def listen(src, a, b, cfg, cache=None, opts=None):
    """Words in [a, b] of src: [(start, end, word, probability)] in the recording's time, and the 50 ms level (dB).
    A word Whisper finds where the level is down at the room's noise, unsure, is dropped (no "Thank you." heard in
    the quiet)."""
    a = max(0.0, a)
    path = None
    if cache:
        import hashlib
        st = os.stat(src)
        k = hashlib.sha1(("%s|%d|%.2f|%.2f|%s" % (os.path.abspath(src), st.st_size, a, b, PROMPT)).encode()).hexdigest()[:16]
        path = os.path.join(cache, k + ".json")
        hit = load(path)
        if hit:
            return [tuple(w) for w in hit["words"]], hit["lv"]
    x = pcm(src, a, b)
    env = envelope(x)
    opts = dict(opts or {})
    if opts.pop("pad_end", False):
        x = np.concatenate([x, np.zeros(RATE, np.float32)])
    if len(x) < RATE:
        return [], {"t0": a, "env": env.tolist()}
    model = analyse.model(cfg)[0]
    segs, _ = model.transcribe(x, language=cfg.get("language") or "en", **dict(WHISPER, **(opts or {})))
    floor = float(np.percentile(env, 10)) if len(env) else -90.0
    words = []
    for s in segs:
        for w in s.words or []:
            i0, i1 = int(w.start / 0.05), max(int(w.start / 0.05) + 1, int(w.end / 0.05))
            loud = float(env[i0:i1].max()) if i1 <= len(env) and i0 < i1 else 0.0
            if (w.probability < 0.35 and loud < floor + 8) or (w.probability < 0.5 and loud < floor + 12):
                continue                                   # unsure and down at the room's quiet: not speech
            if w.end - w.start < 0.02 and w.probability < 0.9:   # "Bye." x10 on one timestamp: not speech
                continue
            words.append((round(a + w.start, 3), round(a + w.end, 3), w.word, round(w.probability, 3)))
    lv = {"t0": a, "env": [round(float(v), 1) for v in env]}
    if path:
        os.makedirs(cache, exist_ok=True)
        save(path, {"words": words, "lv": lv})
    return words, lv


def _real(sents, segs, a, b):
    """Sentences Whisper made up in the quiet: unsure (mean probability under 0.7) and with no whole-day line near
    them (SREday Dallas, Girish Konda: "Hello, everyone." heard in a silence before his real "Hey, good afternoon")."""
    if not segs:
        return sents
    inside = [g for g in segs if g["end"] >= a - 5 and g["start"] <= b + 5]
    out = []
    for x in sents:
        near = any(g["start"] - 2.0 <= x["end"] and g["end"] + 2.0 >= x["start"] for g in inside)
        if x["p"] < 0.65 and not near:
            continue
        out.append(x)
    for k, x in enumerate(out):
        x["next"] = out[k + 1]["start"] if k + 1 < len(out) else None
        x["prev"] = out[k - 1]["end"] if k else None
    return out


def _last_word_end(s):
    """Where the sentence's last word really ends: Whisper often clips it ("it." 0.02 s long)."""
    w = s["words"][-1] if s.get("words") else None
    return max(s["end"], (w[0] + 0.25) if w else s["end"])


def fill(words, segs, a, b):
    """The close listening's words, with what it missed filled in from the whole-day transcript: every whole-day
    line in [a, b] with no close word near it comes in as words spread over its spoken length (marked p=0.5, so a
    filled line never counts as surer than a heard one). SREday Dallas: on some 4-minute windows Whisper went quiet
    for the last 100-160 s, where the whole-day pass heard "Yeah, that's it. Any questions?"."""
    out = list(words)
    starts = sorted(w[0] for w in words)
    import bisect
    for g in segs:
        if g["end"] < a or g["start"] > b or not g.get("text", "").strip():
            continue
        lo, hi = g["start"] - 0.6, segment._spoken_end(g) + 0.6
        k = bisect.bisect_left(starts, lo)
        if k < len(starts) and starts[k] <= hi:
            continue                                      # heard closely: the close words stand
        toks = g["text"].split()
        end = segment._spoken_end(g)
        step = max(0.05, (end - g["start"]) / max(1, len(toks)))
        for i, t in enumerate(toks):
            out.append((round(g["start"] + i * step, 3), round(g["start"] + (i + 1) * step - 0.02, 3), " " + t, 0.5))
    out.sort(key=lambda w: w[0])
    return out


def sentences(words):
    """Words -> sentences: a break after . ! ? (Whisper's punctuation), or a pause of 1.5 s+ (then not 'done').
    Whisper's echo at the very end ("Thank you for watching." again and again, in the applause) is dropped."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1][0] if i + 1 < len(words) else None
        nxt_word = words[i + 1][2].strip() if i + 1 < len(words) else ""
        if re.search(r"[.!?]$", w[2].strip()) or nxt is None or (nxt - w[1] >= 1.5 and not nxt_word[:1].islower()):
            out.append(cur)
            cur = []
    # a long run with no punctuation (Whisper on set-up chatter or a far mic: "gotcha gotcha that's fine okay all right
    # ...") is cut at its own pauses, so a greeting or a Q&A opener inside it stands on its own; none of the pieces
    # counts as a finished sentence (only the run's last word may carry . ! ?)
    fine = []
    for s in out:
        if len(s) <= 25 or sum(1 for w in s if re.search(r"[.!?,]$", w[2].strip())) * 12 >= len(s):
            fine.append(s)
            continue
        cur = [s[0]]
        for w in s[1:]:
            if w[0] - cur[-1][1] >= 0.5:
                fine.append(cur)
                cur = []
            cur.append(w)
        fine.append(cur)
    out = fine
    sents = []
    for k, s in enumerate(out):
        text = "".join(w[2] for w in s).strip()
        sents.append({"start": s[0][0], "end": s[-1][1], "text": text, "norm": segment._norm(text), "words": s,
                      "done": bool(re.search(r"[.!?]$", s[-1][2].strip())), "q": text.endswith("?"),
                      "p": float(np.mean([w[3] for w in s])), "last": segment._norm(s[-1][2]),
                      "next": out[k + 1][0][0] if k + 1 < len(out) else None,
                      "prev": out[k - 1][-1][1] if k else None})
    keep = []
    for k, x in enumerate(sents):
        if ECHO.search(x["norm"]) or x["norm"] == "you":            # never said in a live talk / Whisper's filler
            continue
        same = [y for y in sents[max(0, k - 3):k] if y["norm"] == x["norm"] and len(x["norm"].split()) >= 1]
        if len(same) >= 2:                                           # a third time in a row: Whisper looping
            continue
        keep.append(x)
    for k, x in enumerate(keep):
        x["next"] = keep[k + 1]["start"] if k + 1 < len(keep) else None
        x["prev"] = keep[k - 1]["end"] if k else None
    return keep


def _word_index(sent, tokens):
    """The index of the word where the sentence's `tokens`-th normalised token falls."""
    n = 0
    for i, w in enumerate(sent["words"]):
        if n >= tokens:
            return i
        n += len(segment._norm(w[2]).split())
    return len(sent["words"])


def _part_at(sent, char):
    """The sentence from the word holding character `char` of its norm on."""
    return _part(sent, _word_index(sent, len(sent["norm"][:char].split())))


def _upto(sent, char):
    """The sentence up to (not including) the word holding character `char` of its norm."""
    k = _word_index(sent, len(sent["norm"][:char].split()))
    ws = sent["words"][:k]
    if not ws:
        return None
    text = "".join(w[2] for w in ws).strip()
    return dict(sent, end=ws[-1][1], text=text, norm=segment._norm(text), words=ws, last=segment._norm(ws[-1][2]),
                next=sent["words"][k][0] if k < len(sent["words"]) else sent["next"], done=False, q=False)


def _part(sent, k):
    """The sentence from its k-th word on (the greeting inside "let me adjust this, hello everyone, how are you")."""
    if k <= 0 or k >= len(sent["words"]):
        return sent
    ws = sent["words"][k:]
    text = "".join(w[2] for w in ws).strip()
    return dict(sent, start=ws[0][0], text=text, norm=segment._norm(text), words=ws, inside=True)


# ---- the start --------------------------------------------------------------------------------------------------------
def _other_name(norm, names):
    """ "my name is Ash" when the speaker is Jeremy: someone else introducing themselves."""
    if not names:
        return False
    m = re.search(r"\b(my name is|my name s)\s+([a-z]+)", norm)
    if not m:
        return False
    own = [t for n in names for t in segment._norm(n).split()]
    return not any(segment._name_like(m.group(2), t) for t in own)


def _speaker_named(norm, names):
    """The MC talking about the speaker: their first name heard without "I'm" / "my name is" in front of it."""
    for n in names:
        first = segment._norm(n).split()[:1]
        if first and len(first[0]) >= 3 and re.search(r"\b%s\b" % re.escape(first[0]), norm) and not re.search(
                r"\b(i m|im|i am|my name is|my name s|myself|this is)\s+%s\b" % re.escape(first[0]), norm):
            return True
    return False


NOT_NAME = set("""a an the so going gonna here very really just also currently from with working at in on not sure
happy glad excited part one of now still actually originally trying kind sort like done back""".split()) | segment.COMMON


def _self_intro(norm):
    """ "I'm Kevin", "my name is Kevin", "this is Krishna" in the sentence's first words."""
    head = " ".join(norm.split()[:14])
    if re.search(r"\b(my name is|my name s|i go by|myself)\s+[a-z]+", head):
        return True
    m = re.search(r"\b(i m|im|i am|this is)\s+([a-z]+)", head)
    return bool(m and m.group(2) not in NOT_NAME and not m.group(2).endswith(("ing", "ed", "ly")))


def _own_name(norm, names):
    """ "I'm Girish" / "my name is Girish": the speaker's own first name."""
    for n in names:
        f = segment._norm(n).split()[:1]
        if f and re.search(r"\b(i m|im|i am|my name is|my name s|this is)\s+%s\b" % re.escape(f[0]), norm):
            return True
    return False


def _opens(norm):
    """A sentence that opens a talk: an opener or the speaker's name in its first words."""
    head = " ".join(norm.split()[:14])
    return bool(OPENER.search(head) or HELLO.search(head) or START_MARK.search(" ".join(norm.split()[:4])) or
                _self_intro(norm))


def _start_noise(s, nxt):
    """Said before the talk proper: a mic check, the laptop, "are we good to go?", "I think we can start", a bare
    "Okay." / "All right." / "Thank you." (to the MC or the applause), a short question to the tech desk."""
    n, words = s["norm"], len(s["norm"].split())
    if segment.MICCHECK.search(n) or ((SETUP.search(n) or SETUP2.search(n) or READY.search(n)) and words <= 16):
        return True
    if words <= 5 and not _opens(n) and not HELLO.search(n) and (
            s["done"] or nxt is None or nxt["start"] - s["end"] >= 1.0):
        return True                                   # "Yes, great." "Oh, okay." (not "Yeah, well I | see that ...")
    if FILLER.match(n):
        return True
    if s["q"] and words <= 5 and not _opens(n):
        return True
    return False


def find_start(sents, anchor, names=(), floor=0.0):
    """(time of the first word, the sentence, why, sure). anchor = the coarse start; nothing before floor (the
    previous talk's own cut) is taken."""
    sents = [s for s in sents if s["start"] >= floor - 0.05]
    if not sents:
        return None, None, "no words heard around the start", False
    # the MC's last words before the talk: introduction / applause call / the speaker named in the third person
    mc = [i for i, s in enumerate(sents) if s["start"] <= anchor + 30 and
          (MC.search(s["norm"]) or (_speaker_named(s["norm"], names) and not _opens(s["norm"])))]
    if mc and mc[-1] + 1 < len(sents):
        i, why = mc[-1] + 1, "after the MC (\"%s\")" % sents[mc[-1]]["text"][:50]
    else:
        mc = []
        i = next((k for k, s in enumerate(sents) if s["end"] >= anchor - 1), len(sents) - 1)
        why = "at the coarse start"
        # the greeting can sit just before the coarse start ("Hey, thanks for being here ... I'm Jay")
        while i > 0 and sents[i]["start"] - sents[i - 1]["end"] <= 3 and anchor - sents[i - 1]["start"] <= 30 \
                and _opens(sents[i - 1]["norm"]) and not _start_noise(sents[i - 1], sents[i]):
            i -= 1
            why = "on the greeting before the coarse start"
    skipped, first = [], sents[i]["start"]
    while i < len(sents) - 1 and sents[i]["start"] - first <= 60 and _start_noise(sents[i], sents[i + 1]):
        skipped.append(sents[i]["text"][:40])
        i += 1
    # the speaker's own greeting or name soon after: the MC still talking, or set-up chatter, before it
    # ("We've got Girish coming up next. We're going to talk a lot about ... " - "Hello, everyone.")
    if not (HELLO.search(" ".join(sents[i]["norm"].split()[:10])) or _self_intro(sents[i]["norm"])):
        for j in range(i + 1, len(sents)):
            if sents[j]["start"] - sents[i]["start"] > 40:
                break
            between = sents[i:j]
            head = " ".join(sents[j]["norm"].split()[:12])
            first3 = sents[j]["norm"].split()[:3]
            own = any(segment._norm(n).split()[:1] and segment._norm(n).split()[0] in first3 for n in names) and \
                re.search(r"\b(i m|i am|my name)\b", " ".join(sents[j]["norm"].split()[:6]))
            if HELLO.search(head) or _self_intro(sents[j]["norm"]) or own:
                if any(_opens(x["norm"]) and not _start_noise(x, None) for x in between):
                    break
                if mc or all(not x["done"] or len(x["norm"].split()) <= 12 or SETUP.search(x["norm"]) or
                             SETUP2.search(x["norm"]) for x in between):
                    skipped += [x["text"][:40] for x in between]
                    i = j
                break
    if HELLO.search(" ".join(sents[i]["norm"].split()[:10])) or re.match(r"^(hello|hi|hey)( there| all)?$", sents[i]["norm"]):
        noisy = False
        for j in range(i + 1, len(sents)):
            if sents[j]["start"] - sents[i]["start"] > 35:
                break
            if _start_noise(sents[j], sents[j + 1] if j + 1 < len(sents) else None) or SETUP2.search(sents[j]["norm"]):
                noisy = True
            elif noisy and (HELLO.search(" ".join(sents[j]["norm"].split()[:10])) or _self_intro(sents[j]["norm"])) \
                    and ((sents[j]["norm"].split() or [""])[0] not in JOINING or HELLO.search(sents[j]["norm"])):
                skipped += [x["text"][:40] for x in sents[i:j]]
                i = j
                break
    s = sents[i]
    # "Okay, let me just adjust this, ... hello everyone, how are you" / "right we're gonna go all right everyone
    # thanks for being out here": start on the greeting inside the line
    m = HELLO.search(s["norm"]) or segment.GREETING_STRONG.search(s["norm"])
    if m and (not s["done"] or mc) and 0 < len(s["norm"][:m.start()].split()) <= (40 if mc else 10) and \
            not _opens(s["norm"][:m.start()]) or (
            m and m.start() > 0 and (SETUP.search(s["norm"][:m.start()]) or READY.search(s["norm"][:m.start()]) or
                                     segment.MICCHECK.search(s["norm"][:m.start()]))):
        t = _part_at(s, m.start())
        if t is not s:
            skipped.append(s["text"][:40] + "...")
            s = t
    if len(s["norm"].split()) > 12:
        setups = [m for rx in (SETUP, SETUP2, READY, segment.MICCHECK) for m in rx.finditer(" ".join(
            s["norm"].split()[:25]))]
        if setups:
            cut = max(m.end() for m in setups)
            m2 = START_MARK.search(s["norm"], cut) or HELLO.search(s["norm"], cut)
            if m2 and len(s["norm"][cut:m2.start()].split()) <= 15:
                t = _part_at(s, m2.start())
                if t is not s:
                    skipped.append(s["text"][:40] + "...")
                    s = t
    if skipped:
        why += "; set-up skipped (%s)" % " / ".join(skipped[:3])
    pause = s["start"] - s["prev"] if s.get("prev") is not None and not s.get("inside") and not skipped else 99.0
    first_word = s["norm"].split()[0] if s["norm"] else ""
    head20 = " ".join(s["norm"].split()[:20])
    setup_in = bool(SETUP.search(head20) or SETUP2.search(head20) or READY.search(head20) or
                    segment.MICCHECK.search(head20))
    # sure: a greeting / the name, or right after the MC, or a cold open ("So my talk is about ...", "Who here ...?")
    # after a real pause - but never mid-sentence (no pause and a joining word)
    punctuated = sum(1 for x in sents if x["done"]) >= max(2, len(sents) // 3)
    lower = s["text"][:1].islower() and not s.get("inside") and not _opens(s["norm"])
    k_s = next((k for k, x in enumerate(sents) if x["start"] >= s["start"] - 0.01), None)
    if mc and k_s and not sents[k_s - 1]["done"] and s["start"] - sents[k_s - 1]["end"] < 1.0 and not _opens(s["norm"]):
        lower = True                                  # the MC's run flows straight into it: who said the first words?
    sure = bool((_opens(s["norm"]) or mc or (pause >= 1.5 and first_word not in JOINING)) and not lower and
                not _other_name(s["norm"], names) and not setup_in)
    return s["start"], s, why, sure


# ---- the end ----------------------------------------------------------------------------------------------------------
def over_at(sents, after, names=()):
    """Where the talk proper is over: the Q&A opened, the room has the floor, or the MC is back (also: the MC
    thanking the speaker by name - "Thank you very much, Greg."). (sentence, why) or None."""
    for k, s in enumerate(sents):
        if s["start"] < after:
            continue
        n = s["norm"]
        later = [x for x in sents[k + 1:k + 8] if x["start"] - s["end"] <= 40]
        resumed = any(segment.RESUME.search(x["norm"]) for x in later)
        if (segment.QA_START.search(n) or QA_OPEN.search(n)) and not segment.INVITE.search(n) and not resumed:
            # "I'll wrap it up here and start with questions" ... and on for 30 s ... "So I'll stop there and answer
            # any kind of questions": the last opener of the cluster is where the talk ends
            more = [x for x in sents[k + 1:] if x["start"] - s["end"] <= 60 and
                    (QA_OPEN.search(x["norm"]) or (segment.QA_START.search(x["norm"]) and
                                                   not segment.INVITE.search(x["norm"])))]
            room = [x for x in sents[k + 1:] if x["start"] < (more[-1]["start"] if more else 0) and ROOM.search(x["norm"])]
            if more and not room:
                s = more[-1]
            return s, "the Q&A opens (\"%s\")" % s["text"][:50]
        if segment.ANNOUNCE.search(n):
            return s, "the Q&A is announced (\"%s\")" % s["text"][:50]
        if ROOM.search(n) and not resumed:
            return s, "the room has the floor (\"%s\")" % s["text"][:50]
        if MC.search(n) or (THANKS.search(n) and _speaker_named(n, names)):
            return s, "the MC is back (\"%s\")" % s["text"][:50]
    return None


def ok_end(s, limit=None):
    """May a talk end on this sentence? Finished, a pause after it (unless the Q&A / the MC comes right after: then
    the cut goes between the two), not hanging, not a question to the room."""
    if not s["done"] or not s["norm"]:
        return False, "unfinished"
    if len(s["norm"].split()) < 3 and not (THANKS.search(s["norm"]) or segment.END_MARK.search(s["norm"]) or
                                           segment.CLOSE.search(s["norm"])):
        return False, "too short to end on"
    if s["next"] is not None and s["next"] - s["end"] < GAP_AFTER and not (limit is not None and s["next"] >= limit - 0.05):
        return False, "no pause after it"
    if s["last"] in DANGLING:
        return False, "hangs on \"%s\"" % s["last"]
    if s["q"] and not THANKS.search(s["norm"]):
        return False, "a question to the room"
    if STRIP.match(s["norm"]) or QA_OPEN.search(s["norm"]) or (segment.QA_START.search(s["norm"]) and
                                                                not segment.INVITE.search(s["norm"])):
        return False, "leads into the Q&A"
    if s["p"] < 0.45:
        return False, "unsure words"
    return True, ""


TRAIL = set("yeah yes um uh er ah okay ok so and right like you know".split())


def _trim_tail(s):
    """The sentence without the filler it trails off in ("... that should be all yeah um" -> "... that should be
    all"); None when nothing is left or nothing to trim."""
    ws = list(s["words"])
    while ws and (segment._norm(ws[-1][2]) in TRAIL or not segment._norm(ws[-1][2])):
        ws.pop()
    if not ws or len(ws) == len(s["words"]) or len(ws) < 3:
        return None
    text = "".join(w[2] for w in ws).strip()
    return dict(s, end=ws[-1][1], text=text, norm=segment._norm(text), words=ws, last=segment._norm(ws[-1][2]),
                next=s["words"][len(ws)][0], trimmed=True)


FIRM_BACK = 30.0              # how far back a firm sentence may be taken over a mumbled close (seconds)
UMS = set("um uh er ah erm hmm".split())


def _filler_share(s):
    ws = s["norm"].split()
    return sum(1 for w in ws if w in UMS) / max(1, len(ws))


def _heard_end(s):
    """Whisper itself ended it with . ! ? (not cut there by the Q&A, not trimmed of its filler)."""
    return bool(s["words"] and re.search(r"[.!?]$", s["words"][-1][2].strip())) and not s.get("trimmed") and \
        not s.get("forced")


def _loose(s):
    """A close that trails off: no end heard (a run with no punctuation), filler trimmed off it, many ums, unsure
    words. Marek: "always end on the firm sentence, if it's followed by some mumbling and stuff"."""
    return not _heard_end(s) or _filler_share(s) >= 0.08 or s["p"] < 0.6


def _firm(s):
    return (_heard_end(s) and not s["q"] and len(s["norm"].split()) >= 5 and s["last"] not in DANGLING and
            _filler_share(s) < 0.08 and s["p"] >= 0.6 and not STRIP.match(s["norm"]) and
            not QA_OPEN.search(s["norm"]) and not (s["next"] is not None and s["next"] - s["end"] < GAP_AFTER))


def _firm_before(pool, k):
    """When the end found (pool[k]) is a loose close, the index of the last firm sentence within FIRM_BACK s before
    it, with only loose talk in between; None otherwise (it stays)."""
    if not _loose(pool[k]) or _thanks(pool[k]):
        return None
    for j in range(k - 1, -1, -1):
        if pool[k]["end"] - pool[j]["end"] > FIRM_BACK:
            return None
        if _firm(pool[j]):
            return j
        if not _loose(pool[j]):
            return None                                    # a finished sentence that isn't firm (short ...): leave it
    return None


def _thanks(s):
    return bool(THANKS.search(s["norm"]) and len(s["norm"].split()) <= 12 and s["done"] and
                not (segment.QA_START.search(s["norm"]) and not segment.INVITE.search(s["norm"])))


def find_end(sents, anchor, start=None, ceil=None, names=()):
    """(time of the last word, the sentence, why, sure). anchor = the coarse end; nothing after ceil (the next talk's
    own cut) is taken, but what is heard there still tells where the talk proper is over."""
    if not sents:
        return None, None, "no words heard around the end", False
    lo = max(start or 0, anchor - END_BEFORE)
    split = []
    for x in sents:
        long_run = len(x["norm"].split()) > 12 and (not x["done"] or len(x["norm"].split()) > 25)
        cuts_ = []
        if long_run:
            for rx in (QA_OPEN, segment.QA_START, ROOM, MC, CONCLUDES):
                if rx is segment.QA_START and segment.INVITE.search(x["norm"]):
                    continue
                cuts_ += [(m.start(), True) for m in rx.finditer(x["norm"])]
            cuts_ += [(m.start(), False) for m in THANKS.finditer(x["norm"])]   # "... thank you yeah that's it | any"
        pieces = []
        kept = []
        for c in sorted(set(c for c in cuts_ if c[0] > 0)):
            if kept and c[0] - kept[-1][0] < 25 and kept[-1][1] == c[1]:
                continue                                   # inside the phrase just cut at
            kept.append(c)
        for c, qa_ in sorted(kept, reverse=True):
            if qa_:
                mm = re.search(r"(?:\b(?:so|and|okay|ok|but) )?(?:\b(?:i ll|i will|i m going to|i m gonna|let me|let s|"
                               r"we ll|we can|i can|i d like to|i want to|we have|we ve got|i have|i ve got|we got))? ?$",
                               x["norm"][:c])
                c = mm.start() if mm and mm.group(0).strip() else c
            head, tail_ = _upto(x, c), _part_at(x, c)     # from the back: each phrase starts a piece of its own
            if head is None or tail_ is x:
                continue
            if qa_:
                head["done"] = True                        # the Q&A begins here: what comes before it is finished
                head["forced"] = True                      # ... though Whisper didn't hear it end
            pieces.insert(0, tail_)
            x = head
        split += [x] + pieces
    sents = split
    ov = over_at(sents, lo, names)
    if ov:
        limit, why = ov[0]["start"], ov[1]
        pool = [s for s in sents if s["end"] <= limit + 0.05 and (ceil is None or s["end"] <= ceil)]
        # "Thank you! Any questions?" in one breath: the thank-you part of the line is the end
        m = re.match(r"^(so |okay |ok |and )?(thank you|thanks)( (so|very) much| all| everyone| everybody)?", ov[0]["norm"])
        if m and THANKS.search(ov[0]["norm"]) and ov[0] is not None:
            k = len(m.group(0).split())
            ws = ov[0]["words"][:k]
            if ws:
                pool.append(dict(ov[0], text="".join(w[2] for w in ws).strip(), norm=m.group(0), q=False, done=True,
                                 last=segment._norm(ws[-1][2]), end=ws[-1][1], words=ws,
                                 next=ov[0]["words"][k][0] if k < len(ov[0]["words"]) else None, cut_in_line=True))
    else:
        limit, why = anchor + 2, "at the coarse end"
        pool = [s for s in sents if s["end"] <= limit and (ceil is None or s["end"] <= ceil)]
    if not pool:
        return None, None, why + "; no sentence before it", False
    last = len(pool) - 1
    # the closing thank-you among the last lines is the ideal end; of "Thank you. / Thank you." in the applause the
    # speaker's own, the first
    for k in range(last, max(-1, last - 5), -1):
        x = pool[k]
        if not _thanks(x) and limit - x["end"] <= 45:
            ms = list(re.finditer(r"\b(thank you|thanks)( (so|very) much| all| everyone| everybody| guys| again)*"
                                  r"( for (your time|listening|having me|your attention|coming|joining))?", x["norm"]))
            if ms and len(x["norm"][ms[-1].end():].split()) <= 4 and len(x["norm"][:ms[-1].start()].split()) >= 0 and \
                    re.match(r"^\s*((and|so|yeah|um|uh|okay|ok|any|i guess|if you have|we have)\b\s*)*$",
                             x["norm"][ms[-1].end():]):
                t2 = _upto(x, ms[-1].end() + 1) if ms[-1].end() < len(x["norm"]) else x
                if t2 is not None and THANKS.search(t2["norm"]):
                    t3 = _part_at(t2, ms[-1].start()) if len(t2["norm"][:ms[-1].start()].split()) > 8 else t2
                    s = dict(t2, done=True)
                    return s["end"], s, why + "; ends on the closing thank-you (inside the last line)", True
        if _thanks(pool[k]) and limit - pool[k]["end"] <= 45:
            while k > 0 and _thanks(pool[k - 1]) and pool[k]["start"] - pool[k - 1]["end"] <= 3 and \
                    len(pool[k - 1]["norm"].split()) <= 4:
                k -= 1
            s = pool[k]
            m = list(re.finditer(r"\b(thank you|thanks)( (so|very) much| all| everyone| everybody| guys| again)*"
                                 r"( for (your time|listening|having me|your attention|coming))?", s["norm"]))
            if m and s["norm"][m[-1].end():].strip() and not re.match(
                    r"^\s*(yeah|that s it|so much|for [a-z ]+)?\s*$", s["norm"][m[-1].end():]):
                t2 = _upto(s, m[-1].end() + 1) if m[-1].end() < len(s["norm"]) else None
                if t2 is not None and THANKS.search(t2["norm"]):
                    s = dict(t2, done=True)
            return s["end"], s, why + ("; ends on the closing thank-you" +
                                       (" (the Q&A part of the line cut)" if s.get("cut_in_line") else "")), True
    steps, k = [], last
    while k >= 0 and len(steps) <= MAX_STEPS + 4 and limit - pool[k]["start"] <= BACKTRACK:
        tail_fill = all(STRIP.match(x["norm"]) or FILLER.match(x["norm"]) for x in pool[k + 1:])
        lim = (pool[k + 1]["start"] if k + 1 < len(pool) and tail_fill else limit) if ov else None
        good, bad = ok_end(pool[k], lim)
        if not good and pool[k]["done"] and _trim_tail(pool[k]):
            t2 = _trim_tail(pool[k])                       # the filler it trails off in goes, the sentence stays
            g2, _ = ok_end(dict(t2, next=None if lim is not None and t2["next"] >= (lim or 0) - 0.05 else t2["next"]), lim)
            if g2 or (lim is not None and t2["last"] not in DANGLING):
                pool[k] = t2
                good, bad = True, ""
        if good:
            real_steps = [x for x in steps if not STRIP.match(segment._norm(x.rsplit(" (", 1)[0]))]
            if steps:
                why += "; stripped %s" % " / ".join("\"%s\"" % x[:40] for x in steps[:3])
            s = pool[k]
            f = _firm_before(pool, k)
            if f is not None:
                why += "; ends on the last firm sentence (the mumbled close after it dropped: \"%s\")" % (
                    " ".join(x["text"] for x in pool[f + 1:k + 1])[:60])
                return pool[f]["end"], pool[f], why, True
            ended = (segment.END_MARK.search(s["norm"]) or segment.CLOSE.search(s["norm"]) or ov or
                     (s["next"] is None or s["next"] - s["end"] >= 3.0))
            return s["end"], s, why, bool(len(real_steps) <= MAX_STEPS and ended)
        steps.append(pool[k]["text"] + " (%s)" % bad)
        k -= 1
    # no punctuation to go by (a far mic): the last piece followed by 1.2 s+ of quiet and not hanging - proposed, and
    # flagged for Claude to read
    for k in range(last, -1, -1):
        x = pool[k]
        if limit - x["start"] > BACKTRACK:
            break
        if x["last"] not in DANGLING and len(x["norm"].split()) >= 3 and (
                x["next"] is None or x["next"] - x["end"] >= 1.2 or (ov and x["next"] >= limit - 0.05)) and \
                not STRIP.match(x["norm"]) and not QA_OPEN.search(" ".join(x["norm"].split()[-8:])) and \
                not segment.QA_START.search(" ".join(x["norm"].split()[-8:])):
            f = _firm_before(pool, k)
            if f is not None:
                dropped = " ".join(y["text"] for y in pool[f + 1:k + 1])[:60]
                return pool[f]["end"], pool[f], why + "; ends on the last firm sentence (the mumbled close after it " \
                    "dropped: \"%s\")" % dropped, bool(ov)
            return x["end"], x, why + "; no punctuation heard - ends on the last piece before a pause (check it)", False
    return None, None, why + "; no sentence that may end the talk within %d s" % BACKTRACK, False


def coarse_over(segs, start, end):
    """The Q&A in the cut's whole-day transcript (to know where to listen close up): the earliest Q&A sign in the
    last 40% of the cut that the speaker doesn't move on from. None if there is none."""
    talk = [s for s in segs if start <= s["start"] < end]
    span = end - start
    for k, s in enumerate(talk):
        if s["start"] < start + 0.6 * span:
            continue
        n = segment._norm(s["text"])
        later = [x for x in talk[k + 1:k + 10] if x["start"] - s["end"] <= 60]
        if any(segment.RESUME.search(segment._norm(x["text"])) for x in later):
            continue
        if ((segment.QA_START.search(n) and not segment.INVITE.search(n)) or segment.ANNOUNCE.search(n) or
                ROOM.search(n) or segment.AUDIENCE_THANKS.search(n)):
            return s["start"]
    return None


# ---- placing the edits ---------------------------------------------------------------------------------------------
def quiet_point(lv, t, lo, hi):
    """The quietest 50 ms in [lo, hi] (recording time), nearest t on a tie."""
    env, t0 = np.asarray(lv["env"]), lv["t0"]
    i0, i1 = int((lo - t0) / 0.05), int((hi - t0) / 0.05) + 1
    i0, i1 = max(0, i0), min(len(env), i1)
    if i1 <= i0:
        return t
    seg = env[i0:i1]
    best = float(seg.min())
    cands = [i0 + j for j, v in enumerate(seg) if v <= best + 1.5]
    j = min(cands, key=lambda i: abs(t0 + (i + 0.5) * 0.05 - t))
    return round(t0 + (j + 0.5) * 0.05, 3)


def speech_before(lv, t, lo):
    """None when the moment before t is quiet; else the end of the last quiet 250 ms between lo and t (the start
    moved back to keep words Whisper did not write down), or lo when there is none."""
    env, t0 = np.asarray(lv["env"]), lv["t0"]
    if not len(env):
        return None
    floor = float(np.percentile(env, 10))
    speech = float(np.percentile(env, 70))
    if speech - floor < 12:
        return None                                   # no clear speech/quiet contrast: the level can't tell
    thr = floor + 0.35 * (speech - floor)
    i = int((t - t0) / 0.05)
    pre = env[max(0, i - 12):max(0, i)]               # the 0.6 s before the cut
    if not len(pre) or float(pre.max()) < thr:
        return None
    k0 = max(0, int((lo - t0) / 0.05))
    for j in range(i - 5, k0 - 1, -1):                # back to the last 250 ms of quiet
        if j >= 0 and float(env[j:j + 5].max()) < thr:
            return round(t0 + (j + 5) * 0.05, 3)
    return round(lo, 3)


def _levels(lv):
    """(env, t0, threshold between the room's quiet and speech) or None when the level can't tell them apart."""
    env, t0 = np.asarray(lv["env"]), lv["t0"]
    if len(env) < 20:
        return None
    floor, speech = float(np.percentile(env, 10)), float(np.percentile(env, 70))
    if speech - floor < 12:
        return None
    return env, t0, floor + 0.35 * (speech - floor)


def pause_after(lv, t, reach=2.0, need=0.2):
    """The first pause (need s of quiet) that starts after t - 0.1 and before t + reach: (its start, its end) in
    recording time, or None. Whisper's word times can be a second early (SREday Dallas, Kevin Brown: the cut lost
    "slash teams"); the sound is not."""
    L = _levels(lv)
    if not L:
        return None
    env, t0, thr = L
    n = int(round(need / 0.05))
    i0, i1 = max(0, int((t - 0.1 - t0) / 0.05)), min(len(env) - n, int((t + reach - t0) / 0.05))
    for i in range(i0, i1 + 1):
        if float(env[i:i + n].max()) < thr:
            j = i + n
            while j < len(env) and env[j] < thr:
                j += 1
            return round(t0 + i * 0.05, 3), round(t0 + j * 0.05, 3)
    return None


def pause_before(lv, t, reach=2.0, need=0.2):
    """The last pause (need s of quiet) that ends after t - reach and before t + 0.1: (its start, its end)."""
    L = _levels(lv)
    if not L:
        return None
    env, t0, thr = L
    n = int(round(need / 0.05))
    i1, i0 = min(len(env), int((t + 0.1 - t0) / 0.05)), max(n, int((t - reach - t0) / 0.05))
    for i in range(i1, i0 - 1, -1):                 # i = the end of the quiet run
        if float(env[i - n:i].max()) < thr:
            j = i - n
            while j > 0 and env[j - 1] < thr:
                j -= 1
            return round(t0 + j * 0.05, 3), round(t0 + i * 0.05, 3)
    return None


def edge_level(lv, t, width=0.1):
    env, t0 = np.asarray(lv["env"]), lv["t0"]
    i = int((t - t0) / 0.05)
    k = max(1, int(width / 0.05))
    seg = env[max(0, i - k):i + k]
    return float(seg.max()) if len(seg) else -90.0


def refine(run, key, cut, names=(), log=None):
    """The talk's sharp edges, from close listening around its coarse ones. Returns a record:
    {"start", "end" (recording time, the first/last part), "first", "last" (the sentences), "why_start",
    "why_end", "sure", "flags", "at"}."""
    cfg = run.cfg
    lead, tail = float(cfg.get("end_lead_seconds", LEAD)), float(cfg.get("end_tail_seconds", TAIL))
    parts = cut["parts"]
    p0, p1 = parts[0], parts[-1]
    others = [p for c in run.cuts()["cuts"] if c["key"] != key for p in c["parts"]]
    floor = max([p["end"] for p in others if p["rec"] == p0["rec"] and p["end"] <= p0["start"] + 1] + [0.0])
    dur = ((run.state["recordings"].get(p1["rec"]) or {}).get("probe") or {}).get("duration") or (p1["end"] + END_AFTER)
    ceil = min([p["start"] for p in others if p["rec"] == p1["rec"] and p["start"] >= p1["end"] - 1] + [dur])
    src0, src1 = run.rec_path(p0["rec"]), run.rec_path(p1["rec"])
    flags, notes = [], []          # flags stop the talk for Claude; notes are for the brief (the check after the cut
    #                                hears whether a tight edge really cuts into a word)

    # the start
    wa, wb = p0["start"] - START_BEFORE, min(p0["start"] + START_AFTER, p0["end"])   # heard into the talk before too
    cache = os.path.join(run.dir, "rec", "ends-words")
    words, lv0 = listen(src0, wa, wb, cfg, cache)
    words = fill(words, run.transcript_segments(p0["rec"]) if hasattr(run, "transcript_segments") else [], wa, wb)
    ss = _real(sentences(words), run.transcript_segments(p0["rec"]) if hasattr(run, "transcript_segments") else [],
               wa, wb)
    t_s, s_s, why_s, sure_s = find_start(ss, p0["start"], names, floor)
    if t_s is None:
        t_s, why_s = p0["start"], why_s + " - the coarse start kept"
    # the pause before the first word - never further back than the word before it (Bailey's "thank you, thank you"
    # to the MC stays out)
    prev = [w for w in words if w[1] <= t_s + 0.05 and w[0] < t_s - 0.05]
    prev_end = max([w[1] for w in prev] + [floor, wa])
    pb = pause_before(lv0, t_s, reach=max(0.3, t_s - prev_end + 0.3))
    if pb and pb[1] >= prev_end - 0.3:
        start = max(pb[0], floor, prev_end, pb[1] - lead)
    elif t_s - prev_end >= 0.15:
        start = quiet_point(lv0, max(prev_end + 0.05, t_s - lead), prev_end + 0.05, t_s - 0.05)
    else:
        start = max(floor, t_s - 0.05)
        notes.append("start: no gap at all before \"%s\" - check it does not cut into a word"
                     % ((s_s or {}).get("text") or "")[:40])
    # the whole-day transcript has a greeting just before that the close listening did not hear (SREday Dallas,
    # Girish Konda: "Hey, good afternoon. I'm Girish" - the close pass only heard "Girish, I'm currently ..."):
    # start on it, and say so
    coarse = run.transcript_segments(p0["rec"]) if hasattr(run, "transcript_segments") else []
    heard = " ".join(x["norm"] for x in ss if abs(x["start"] - t_s) <= 15)
    missed = [g for g in coarse if max(floor, t_s - 12) <= g["start"] < t_s - 0.5 and g["end"] > t_s - 8 and
              (HELLO.search(segment._norm(g["text"])) or _own_name(segment._norm(g["text"]), names)) and
              not (HELLO.search(segment._norm(g["text"])) and HELLO.search(segment._norm(g["text"])).group(0) in heard)]
    if missed:
        g = missed[0]
        start = quiet_point(lv0, g["start"] - lead, max(floor, g["start"] - lead - 0.5), g["start"] + 0.2)
        flags.append("start: the whole-day transcript has \"%s\" just before, the close listening did not hear it - "
                     "started there (check it)" % g["text"][:50])
    elif not sure_s:
        flags.append("start: no greeting or name heard - \"%s\"" % ((s_s or {}).get("text") or "")[:60])

    # the end: listen around the Q&A if the whole-day transcript shows one, else around the coarse end
    segs = run.transcript_segments(p1["rec"]) if hasattr(run, "transcript_segments") else []
    qa = coarse_over(segs, parts[0]["start"] if len(parts) == 1 else p1["start"], p1["end"]) if segs else None
    # PLATFORMday SF, Wassim Melakhessou: the first "great question" was a pause mid-talk, 8 minutes before the end -
    # the end is looked for around the agreed one; an earlier question only gets a flag
    anchor = p1["end"]
    ea = max(p1["start"] + 60, anchor - END_BEFORE)     # not tied to the start: the words cache stays valid
    eb = min(dur, anchor + END_AFTER)                          # heard into the next talk too
    words, lv1 = listen(src1, ea, eb, cfg, cache)
    words = fill(words, segs, ea, eb)
    se = _real(sentences(words), segs, ea, eb)
    t_e, s_e, why_e, sure_e = find_end(se, anchor, start if len(parts) == 1 else p1["start"], ceil, names)
    if t_e is None:
        end, why_e = p1["end"], why_e + " - the coarse end kept"
        flags.append("end: " + why_e)
    else:
        nxt = s_e.get("next")
        t_e = _last_word_end(s_e)
        # the first pause after the last word; Whisper's times can be a second early, so the pause may come up to
        # 1 s after where Whisper puts the next word, but never after that word is over
        later = [w for w in words if w[0] >= t_e - 0.05]
        nxt_w = later[0] if later else None
        reach = min(2.0, (nxt_w[0] - t_e + 1.0) if nxt_w else 2.0, (nxt_w[1] - t_e) if nxt_w else 2.0) \
            if nxt_w else 2.0
        pa = pause_after(lv1, t_e, reach=max(0.3, reach))
        if pa and pa[0] <= ceil:
            end = min(pa[0] + tail, max(pa[0] + 0.1, pa[1] - 0.1), ceil)   # into the pause, never into the next word
        elif nxt_w is None or nxt_w[0] - t_e >= 0.15:
            hi = min(t_e + tail + 0.3, (nxt_w[0] - 0.05) if nxt_w else 1e9, ceil)
            end = quiet_point(lv1, min(t_e + tail, hi), t_e + 0.05, hi)
        else:
            end = min(ceil, t_e + 0.05)
            notes.append("end: no gap at all after \"%s\" - check it does not cut a word off" % s_e["text"][-40:])
        if not sure_e:
            flags.append("end: not sure the talk is over here - \"%s\"" % (s_e["text"][-60:]))
        # much of the talk dropped before the Q&A (Whisper missed the "That" of "That concludes, any questions?" and
        # the last 30 s of a summary went): Claude reads it
        dropped = [x for x in se if x["start"] >= t_e and x["start"] < min(p1["end"], ceil) - 1 and
                   not (segment.QA_START.search(x["norm"]) or QA_OPEN.search(x["norm"]) or ROOM.search(x["norm"]))]
        n_drop = sum(len(x["norm"].split()) for x in dropped)
        if p1["end"] - end > 20 and n_drop >= 40:
            flags.append("end: %d s and ~%d words of speech before the agreed end are left out - the talk's last "
                         "lines, or already the Q&A?" % (p1["end"] - end, n_drop))
    if qa and qa < end - 120:
        flags.append("end: the whole-day transcript has a question at %s, %d min before this end - a Q&A left in the "
                     "cut, or a question mid-talk?" % (segment.stamp(qa), (end - qa) // 60))
    rec = {"start": round(start, 2), "end": round(end, 2), "first": (s_s or {}).get("text", ""),
           "last": (s_e or {}).get("text", ""), "why_start": why_s, "why_end": why_e,
           "moved": [round(start - p0["start"], 1), round(end - p1["end"], 1)],
           "edge_db": [round(edge_level(lv0, start), 1), round(edge_level(lv1, end), 1)],
           "sure": not flags, "flags": flags, "notes": notes, "by": "rules",
           "around_start": _context(ss, s_s, 6, 6), "around_end": _context(se, s_e, 10, 5)}
    if log:
        log("%s: starts %s \"%s\", ends %s \"%s\"%s" % (key, segment.stamp(start), rec["first"][:40],
                                                        segment.stamp(end), rec["last"][-40:],
                                                        " - " + "; ".join(flags) if flags else ""))
    return rec


def _context(sents, chosen, before, after):
    """The sentences around the chosen one: [[start, text, chosen?]]."""
    if not sents:
        return []
    k = next((i for i, s in enumerate(sents) if chosen and abs(s["start"] - chosen["start"]) < 0.01), None)
    if k is None:
        return [[s["start"], s["text"][:160], False] for s in sents[:before + after]]
    return [[s["start"], s["text"][:160], i == k] for i, s in enumerate(sents) if k - before <= i <= k + after]


def _tokens(text):
    return [w for w in segment._norm(text).split() if w]


def verify(lean, rec, cfg, window=20.0):
    """The cut heard again at both edges: its first words are the planned first sentence's, its last words the
    planned last sentence's, no Q&A / question from the room in its last seconds, and both edges quiet.
    Returns (problems, notes)."""
    problems, notes = [], []
    dur = media.probe(lean)["duration"]
    head, lh = listen(lean, 0.0, min(window, dur), cfg, opts={"vad_filter": False})
    tail, lt = listen(lean, max(0.0, dur - window), dur, cfg, opts={"vad_filter": False, "pad_end": True})
    tail = [w for w in tail if w[0] < dur - 0.05]
    want_first, want_last = _tokens(rec.get("first", ""))[:3], _tokens(rec.get("last", ""))[-3:]
    got_first = _tokens(" ".join(w[2] for w in head[:8]))
    got_last = _tokens(" ".join(w[2] for w in tail[-10:]))
    hit = lambda want, got: sum(1 for w in want if w in got) >= min(2, len(want))   # Whisper spells a word differently
    if want_first and not hit(want_first, got_first):
        problems.append("the cut starts on \"%s\", not on \"%s\"" % (" ".join(got_first[:6]), rec.get("first", "")[:50]))
    quiet_thanks = False
    if want_last and not hit(want_last, got_last) and len(want_last) <= 3 and THANKS.search(" ".join(want_last)):
        L = _levels(lt)                                   # a quiet last "Thank you" Whisper didn't write down
        if L and tail:
            env, t0, thr = L
            i = int((tail[-1][1] - t0) / 0.05)
            quiet_thanks = int((env[i:] >= thr - 6).sum()) >= 5
            if quiet_thanks:
                notes.append("the last \"%s\" is heard as sound only (quiet)" % rec.get("last", "")[-30:])
    if want_last and not hit(want_last, got_last) and not quiet_thanks:
        problems.append("the cut ends on \"%s\", not on \"%s\"" % (" ".join(got_last[-6:]), rec.get("last", "")[-50:]))
    last_text = segment._norm(" ".join(w[2] for w in tail if w[0] >= dur - 8))
    if (segment.QA_START.search(last_text) and not segment.INVITE.search(last_text)) or ROOM.search(last_text):
        problems.append("the Q&A is heard in the cut's last seconds (\"%s\")" % last_text[-60:])
    for what, lv in (("starts", lh), ("ends", lt)):
        L = _levels(lv)
        if L:
            env, t0, thr = L
            edge = env[:2] if what == "starts" else env[-3:]
            # a sound at the edge is a problem only when the word heard there is not the planned one (a talk can
            # start right on its first word)
            wrong = (got_first[:1] and got_first[0] not in want_first[:2]) if what == "starts" else \
                (got_last[-1:] and got_last[-1] not in want_last[-2:])
            if float(edge.min()) >= thr and wrong:
                problems.append("the cut %s in the middle of a sound (a word cut into?)" % what)
    for what, lv, t in (("start", lh, 0.0), ("end", lt, dur)):
        env = np.asarray(lv["env"])
        if len(env) > 4:
            floor = float(np.percentile(env, 10))
            edge = float(env[:2].max()) if what == "start" else float(env[-2:].max())
            if edge > floor + 20:
                notes.append("the %s edge is %.0f dB over the room's quiet (a word or applause there?)" % (what, edge - floor))
    notes.append("first heard: \"%s\"; last heard: \"%s\"" % (" ".join(got_first[:6]), " ".join(got_last[-6:])))
    return problems, notes


def apply(cut, rec):
    """cut's parts with the sharp edges (the first part's start, the last part's end)."""
    parts = [dict(p) for p in cut["parts"]]
    parts[0]["start"], parts[-1]["end"] = rec["start"], rec["end"]
    return parts


def save(path, data):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
