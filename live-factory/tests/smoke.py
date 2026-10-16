"""Offline smoke test of the LIVE talk factory - no Descript calls.

  env/bin/python tests/smoke.py              (macOS: real speech from `say` + real Whisper, tiny.en model)
  env/bin/python tests/smoke.py --no-whisper (anywhere: tones, and the transcript is written from the script)
  --keep leaves the temp folder (its settings.smoke.yml works as LIVE_FACTORY_SETTINGS for `serve`)

Builds a fixture brand repo with one event (three talks on one track, a break), a synthetic "day" recording -
silence, an MC introducing each speaker, the talks, a one-sided (left only) last talk - then: schedule, add,
analyse, draft cuts (checked against the truth), one-sided stereo detection, a cut with the stereo fix (both
channels loud afterwards), the brief, and the YouTube title. Everything happens in a temp folder.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

NO_WHISPER = "--no-whisper" in sys.argv or sys.platform != "darwin" or not shutil.which("say")
TMP = tempfile.mkdtemp(prefix="live-factory-smoke-")
FAILS = []


def check(what, ok, detail=""):
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what, (" - " + str(detail)) if detail else ""))
    if not ok:
        FAILS.append(what)


SCRIPT = [  # (who, text, channels) - channels "both", "left" or "quiet" (-35 dB, a mic left far away)
    ("silence", 20, "both"),
    ("mc", "Good morning everyone and welcome to the conference. Please welcome our first speaker, Alice Walker.", "both"),
    ("Alice Walker", "Hi, I'm Alice. Today I want to talk about reliability patterns for production Kubernetes "
     "clusters. Pod disruption budgets, readiness probes and careful rollout strategies keep the lights on. We will "
     "look at three incidents from our platform, and how small changes to our deployment pipeline removed a whole "
     "class of outages. That's all I have. Thank you! Any questions? Yes, in the back. Great question, we use "
     "canary rollouts for that, with automatic rollback.", "both"),
    ("silence", 25, "both"),
    ("mc", "Thank you Alice. Next up, please welcome Bob Martinez and Carol Chen.", "both"),
    ("Bob Martinez & Carol Chen", "Hello everyone, I'm Bob, and this is Carol. We are going to show how to build "
     "observability pipelines without tears. Any questions so far? No? Okay, moving on. Most teams collect far too much "
     "telemetry. We will walk through "
     "sampling, routing and enrichment of traces and logs, and show the dashboards our on call engineers open at "
     "three in the morning. Thank you very much.", "both"),
    ("silence", 40, "both"),
    ("mc", "Welcome back from the break. Please give it up for David Okafor.", "both"),
    ("David Okafor", "Hey, I'm David. My talk is about incident response automation with runbooks. Every alert should "
     "come with a clear next step. I will show how we turned wiki pages into executable runbooks, how we test them, "
     "and how we keep a human in the loop. We cut our time to mitigate by half. Thanks, any questions?", "left"),
    ("silence", 15, "both"),
    ("mc", "Thank you David. Next, please welcome Erin Quigley.", "both"),
    ("Erin Quigley", "Hi, I'm Erin. I will talk about chaos engineering experiments with game days. We inject "
     "latency and failures into staging, measure the blast radius, and fix the weakest dependency first. Thank you.",
     "quiet"),
    ("silence", 20, "both"),
]
TALKS = [  # name, title, abstract
    ("Alice Walker", "Kubernetes Reliability Patterns for Production Clusters",
     "Pod disruption budgets, readiness probes, rollout strategies and incidents from our Kubernetes platform."),
    ("Bob Martinez & Carol Chen", "Observability Pipelines Without Tears",
     "Telemetry sampling, routing and enrichment of traces and logs, and dashboards for on-call engineers."),
    ("David Okafor", "Incident Response Automation with Runbooks",
     "Executable runbooks for every alert, testing them, and keeping a human in the loop to mitigate faster."),
    ("Erin Quigley", "Chaos Engineering Game Days",
     "Chaos experiments: inject latency and failures into staging, measure the blast radius, fix weak dependencies."),
    ("Frank Missing", "Database Migrations at Scale",
     "Zero-downtime schema migrations, backfills and online index builds on very large Postgres clusters."),
]


def ff(*args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("ffmpeg failed: " + r.stderr[-400:])


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout
    return float(out.strip())


def build_recording(folder):
    """The synthetic day: returns (path, truth {who: (start, end)})."""
    pieces, truth, at = [], {}, 0.0
    for n, (who, what, ch) in enumerate(SCRIPT):
        wav = os.path.join(folder, "p%02d.wav" % n)
        pan = {"both": "pan=stereo|c0=c0|c1=c0", "left": "pan=stereo|c0=c0|c1=0*c0",
               "quiet": "pan=stereo|c0=c0|c1=c0,volume=0.018"}[ch]
        if who == "silence":
            ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(what), wav)
        elif NO_WHISPER:
            secs = max(4, len(what.split()) / 2.5)
            ff("-f", "lavfi", "-i", "sine=frequency=%d:sample_rate=48000" % (300 + 40 * n), "-t", "%.2f" % secs,
               "-af", "volume=0.3," + pan, wav)
        else:
            aiff = os.path.join(folder, "p%02d.aiff" % n)
            subprocess.run(["say", "-o", aiff, what], check=True)
            ff("-i", aiff, "-ar", "48000", "-af", pan, wav)
        d = duration(wav)
        if who not in ("silence", "mc"):
            truth[who] = (at, at + d)
        pieces.append((wav, who, what, at, d))
        at += d
    listing = os.path.join(folder, "list.txt")
    with open(listing, "w") as f:
        f.write("".join("file '%s'\n" % p[0] for p in pieces))
    audio = os.path.join(folder, "day.wav")
    ff("-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", audio)
    video = os.path.join(folder, "DAY1_CAM.mp4")
    ff("-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-i", audio, "-t", "%.2f" % at, "-c:v", "libx264",
       "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video)
    return video, truth, pieces


def fixture_repo(root):
    ev = os.path.join(root, "testbrand", "2026-testcity-q4")
    os.makedirs(os.path.join(ev, "_db"))
    with open(os.path.join(ev, "metadata.yml"), "w") as f:
        f.write('brand_name: "TESTday"\nbrand_domain: "testday.example"\nstart_time: "2026-11-05T09:00:00+00:00"\n'
                'city_name: "Testcity"\ndate_string: "November 5, 2026"\ndays: 1\nrooms:\n- main stage\ncfp_url: https://cfp.example/e/testday\n'
                'youtube_url: https://www.youtube.com/@testday\nbreaks:\n- title: Coffee\n  duration: 1\n  talks_before: 2\n')
    import csv
    with open(os.path.join(ev, "_db", "talks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["YouTube", "status", "name", "track", "day", "organization", "title", "duration", "abstract"])
        for name, title, abstract in TALKS:
            w.writerow(["", "talk", name, "1", "1", "Acme", title, "2", abstract])
        w.writerow(["", "draft", "Not Confirmed", "1", "1", "", "Some Draft", "2", ""])
    settings = os.path.join(root, "settings.smoke.yml")
    with open(settings, "w") as f:
        f.write("github_root: %s\nbrands:\n  testbrand: {repo: testbrand}\nwork_root: %s\nwhisper_model: tiny.en\n"
                "frame_every_seconds: 30\ntalk_min_minutes: 0.2\ntalk_max_minutes: 3\n" % (json.dumps(root), json.dumps(os.path.join(root, "work"))))
    return settings


def fake_transcript(run, rid, pieces):
    """--no-whisper: the transcript Whisper would give, one segment per sentence-ish piece."""
    from live_factory import analyse
    import re
    segs = []
    for _, who, what, at, d in pieces:
        if who == "silence":
            continue
        sentences = [x for x in re.split(r"(?<=[.!?])\s+", what) if x]
        total = sum(len(x) for x in sentences)
        for x in sentences:                         # one segment per sentence, timed by its length - as Whisper does
            seg = d * len(x) / total
            segs.append({"start": round(at, 2), "end": round(at + seg, 2), "text": x, "lp": -0.3, "nsp": 0.01})
            at += seg
    folder = run.rec_dir(rid)
    analyse._write_json(os.path.join(folder, "transcript.json"), {"segments": segs})
    run.rset(rid, "transcript", "done", note="fake (--no-whisper)")


def main():
    print("LIVE talk factory smoke test in %s (%s)" % (TMP, "tones, fake transcript" if NO_WHISPER else "say + Whisper"))
    os.environ["LIVE_FACTORY_SETTINGS"] = fixture_repo(TMP)
    from live_factory import events, media, pipeline, segment, stereo, youtube

    cfg = pipeline.settings()
    ev = events.event(cfg, "testbrand-2026-testcity-q4")
    check("event title (with the quarter, as on the channels)", ev["title"] == "TESTday Testcity 2026 Q4", ev["title"])
    sch = events.schedule(ev)
    items = sch["1"]["items"]
    check("schedule: 5 talks + break in order", [x["kind"] for x in items] == ["talk", "talk", "break", "talk", "talk", "talk"],
          [(x["start"], x["kind"], x["key"]) for x in items])
    check("schedule times", [x["start"] for x in items] == ["09:00", "09:02", "09:04", "09:05", "09:07", "09:09"])
    check("draft rows stay out", all(t["key"] != "Not Confirmed" for t in events.talks(ev)))

    rec_dir = os.path.join(TMP, "card")
    os.makedirs(rec_dir)
    video, truth, pieces = build_recording(rec_dir)
    raw_hash = media.sampled_hash(video)
    media.notify = lambda *a: None                    # no desktop notifications from the test
    run = pipeline.Run(ev["id"])

    # disk space: a job that does not fit never starts
    run.cfg["disk_reserve_gb"] = 10 ** 6
    try:
        run.add([rec_dir], day=1)
        check("not enough space: refused", False)
    except pipeline.SpaceError as ex:
        check("not enough space: refused, nothing added", not run.state["recordings"] and run.state.get("space_warning"), ex)
    run.cfg["disk_reserve_gb"] = 0

    ids = run.add([rec_dir], day=1)
    check("add: one recording from the card folder", ids == ["r1"], ids)
    check("space warning cleared", not run.state.get("space_warning"))
    if NO_WHISPER:
        run.copy_in("r1")
        fake_transcript(run, "r1", pieces)
    run.analyse()
    r = run.state["recordings"]["r1"]
    check("analyse: every step done", all((r["steps"].get(s) or {}).get("status") == "done" for s in pipeline.REC_STEPS),
          {s: (r["steps"].get(s) or {}).get("status") for s in pipeline.REC_STEPS})
    check("proxy not needed for an H.264 mp4", "not needed" in (r["steps"]["proxy"].get("note") or ""))

    # the raw recording: the original untouched, the backup copy checked, read-only and protected
    copy = run.rec_path("r1")
    check("original untouched", os.path.exists(video) and media.sampled_hash(video) == raw_hash)
    check("backup copy matches the original", media.sampled_hash(copy) == raw_hash and r.get("hash") == raw_hash)
    check("backup copy is read-only", not os.access(copy, os.W_OK) or os.name == "nt" and not (os.stat(copy).st_mode & 0o200))
    for target in (copy, video, copy + ".4242.part.mp4"):
        try:
            media.cut([(copy, 1, 5)], target)
            check("writing over a raw file refused (%s)" % os.path.basename(target), False)
        except PermissionError:
            check("writing over a raw file refused (%s)" % os.path.basename(target), True)
    check("original still untouched after all that", media.sampled_hash(video) == raw_hash)

    lv = run.levels("r1")
    s_david = stereo.check(lv, *truth["David Okafor"], cfg)
    s_alice = stereo.check(lv, *truth["Alice Walker"], cfg)
    check("stereo: left-only talk found", s_david["fix"] == "left", s_david["note"])
    check("stereo: normal talk left alone", s_alice["fix"] is None, s_alice["note"])
    if not NO_WHISPER:
        tj = json.load(open(os.path.join(run.rec_dir("r1"), "transcript.json")))
        text = " ".join(s["text"] for s in tj["segments"]).lower()
        check("whisper heard the speakers' names", "alice" in text and "okafor" in text.replace("o'kafor", "okafor"),
              text[:160])

    data = segment.draft(run)
    found = {c["key"]: c for c in data["cuts"]}
    clear = ["Alice Walker", "Bob Martinez & Carol Chen", "David Okafor"]
    check("draft: a cut per clearly recorded talk", set(clear) <= set(found), sorted(found))
    # where Alice's Q&A starts, by the transcript: her cut must end right before it (her "Thank you!" kept)
    segs_all = json.load(open(os.path.join(run.rec_dir("r1"), "transcript.json")))["segments"]
    qa = next(sg for sg in segs_all if truth["Alice Walker"][0] < sg["start"] < truth["Alice Walker"][1]
              and "questions" in sg["text"].lower())
    for who in clear:
        c = found.get(who)
        if not c:
            continue
        ts, te = truth[who]
        if who == "Alice Walker":
            te = qa["start"] if "thank" not in qa["text"].lower() else qa["end"]
        p = c["parts"][0]
        check("draft: %s start within 6 s" % who, abs(p["start"] - ts) <= 6, "cut %.1f, truth %.1f (%s)" % (p["start"], ts, c["reason"]))
        check("draft: %s end within 8 s" % who, abs(p["end"] - te) <= 8, "cut %.1f, truth %.1f" % (p["end"], te))
        check("draft: %s has no flags" % who, not c.get("flags"), c.get("flags"))
    check("draft: Alice starts on \"Hi, I'm Alice\"", "introduces themselves" in found["Alice Walker"]["reason"],
          found["Alice Walker"]["reason"])
    check("draft: Alice's closing Q&A is cut off", "Q&A from" in found["Alice Walker"]["reason"] and "dropped" in
          found["Alice Walker"]["reason"], found["Alice Walker"]["reason"])
    check("draft: Bob's mid-talk question keeps the talk whole", "mid-talk - kept" in found["Bob Martinez & Carol Chen"]["reason"]
          or "carries on" in found["Bob Martinez & Carol Chen"]["reason"], found["Bob Martinez & Carol Chen"]["reason"])
    erin = found.get("Erin Quigley")
    check("badly recorded talk flagged", (erin and any("quiet" in f or "words" in f or "sound" in f for f in erin.get("flags", [])))
          or any(x["key"] == "Erin Quigley" for x in data["skipped"]), erin and erin.get("flags"))
    check("talk that was never recorded flagged", any(x["key"] == "Frank Missing" and "not recorded" in x["why"]
                                                      for x in data["skipped"]), data["skipped"])
    # a cut with the wrong name on it (a swapped slot) is caught by what is said in it
    wrong = {"key": "David Okafor", "parts": [dict(found["Alice Walker"]["parts"][0])]}
    flags = segment.assess(run, wrong)["flags"]
    check("swapped talk flagged by its words", any("sounds like Alice Walker" in f for f in flags), flags)

    check("draft cuts validate", not [x for x in segment.validate(run, data) if x.startswith("ERROR")],
          segment.validate(run, data))
    for c in data["cuts"]:
        c["approved"] = True
    run.save_cuts(data)
    bad = {"cuts": [{"key": "Nobody", "parts": [{"rec": "r9", "start": 5, "end": 1}]}]}
    try:
        run.save_cuts(bad)
        check("bad cuts refused", False)
    except ValueError as ex:
        check("bad cuts refused", "Nobody" in str(ex) and "r9" in str(ex))
    overlap = {"cuts": [{"key": "Alice Walker", "parts": [{"rec": "r1", "start": 10, "end": 100}]},
                        {"key": "David Okafor", "parts": [{"rec": "r1", "start": 90, "end": 150}]}]}
    check("overlapping cuts are an error", any(x.startswith("ERROR") and "overlap" in x for x in segment.validate(run, overlap)))

    keys = run.sync_talks()
    check("approved cuts become talks", set(clear) <= set(keys), keys)
    cut = found["David Okafor"]
    st = run.stereo_for(cut)
    out = os.path.join(TMP, "david.mp4")
    p = cut["parts"][0]
    media.cut([(run.rec_path("r1"), p["start"], p["end"])], out, st["af"])
    lv2 = media.levels(out)
    after = stereo.check(lv2, 0, len(lv2["left"]), cfg)
    check("cut with the stereo fix: both channels loud", after["fix"] is None and after["right_db"] > -40, after["note"])
    check("cut length", abs(media.probe(out)["duration"] - (p["end"] - p["start"])) < 1.0)
    two = os.path.join(TMP, "two-parts.mp4")
    media.cut([(run.rec_path("r1"), 10, 20), (run.rec_path("r1"), 30, 45)], two)
    check("a talk in two parts is joined", abs(media.probe(two)["duration"] - 25) < 1.0, media.probe(two)["duration"])
    final = os.path.join(TMP, "david-final.mp4")
    how = media.normalize(out, final, -14, -1.5, crf=cfg.get("final_crf", 21), preset=cfg.get("final_preset", "medium"))
    lufs, _ = media.loudness(final)
    check("final: lean mp4, no bigger than its source", os.path.getsize(final) <= os.path.getsize(out) and
          media.probe(final)["vcodec"] == "h264", "%s, %.1f MB from %.1f MB" % (how, os.path.getsize(final) / 1e6, os.path.getsize(out) / 1e6))
    check("final: at YouTube loudness", lufs is not None and abs(lufs + 14) < 1.5, lufs)

    # extra footage around a cut: 30 s before, 60 s after, never into a neighbour's own cut
    talk_keys = run.sync_talks()
    pad_parts, before, after = run.padded("Bob Martinez & Carol Chen")
    bob, alice_c, david_c = (found[k]["parts"][0] for k in ("Bob Martinez & Carol Chen", "Alice Walker", "David Okafor"))
    check("extra footage before stops at the previous talk's cut", abs(pad_parts[0]["start"] - max(alice_c["end"], bob["start"] - 30)) < 0.2,
          "%.1f (Alice ends %.1f)" % (pad_parts[0]["start"], alice_c["end"]))
    check("extra footage after stops at the next talk's cut", abs(pad_parts[-1]["end"] - min(david_c["start"], bob["end"] + 60)) < 0.2,
          "%.1f (David starts %.1f)" % (pad_parts[-1]["end"], david_c["start"]))
    run.cfg.update(pad_before_seconds=30, pad_after_seconds=60)

    # black bars: a 16:9 deck boxed inside the frame is zoomed back to full HD; a 4:3 deck and a full frame are not
    vids = os.path.join(TMP, "bars")
    os.makedirs(vids)
    def boxed(name, w, h):
        out = os.path.join(vids, name)
        ff("-f", "lavfi", "-i", "testsrc2=size=%dx%d:rate=25" % (w, h), "-t", "12",
           "-vf", "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", out)
        return out
    wide, square, full = boxed("wide.mp4", 1088, 612), boxed("43.mp4", 800, 600), boxed("full.mp4", 1280, 720)
    crop, why = media.black_bars(wide, 0, 12, samples=6)
    check("black bars around a 16:9 deck found", crop and abs(crop[0] - 1088) <= 8 and abs(crop[1] - 612) <= 8, (crop, why))
    c43, why43 = media.black_bars(square, 0, 12, samples=6)
    check("black bars around a 4:3 deck found too", c43 and abs(c43[0] - 800) <= 8, (c43, why43))
    cf, whyf = media.black_bars(full, 0, 12, samples=6)
    check("a full-frame picture is left alone", cf is None, whyf)
    for name, src, box, expect in (("16:9", wide, crop, (1280, 720)), ("4:3", square, c43, (960, 720))):
        zoomed = os.path.join(vids, "zoomed-%s.mp4" % name.replace(":", ""))
        media.cut([(src, 0, 12)], zoomed, crop=box)
        zi = media.probe(zoomed)
        after, _ = media.black_bars(zoomed, 0, 12, samples=6)
        r = media.run([media.tool("ffmpeg"), "-hide_banner", "-ss", "6", "-i", zoomed, "-frames:v", "3", "-vf",
                       "cropdetect=limit=0.08:round=2:reset=0", "-f", "null", "-"])
        import re as _re
        seen = _re.findall(r"crop=(\d+):(\d+)", r.stderr)[-1]
        check("%s deck zoomed to fit: full frame, slides %s" % (name, "x".join(map(str, expect))),
              (zi["width"], zi["height"]) == (1280, 720) and after is None
              and abs(int(seen[0]) - expect[0]) <= 8 and abs(int(seen[1]) - expect[1]) <= 8, (zi["width"], zi["height"], seen))

    # flag for a redo: the next run starts the talk again (as "<talk> (redo 2)" in Descript), once
    t = run.state["talks"]["David Okafor"]
    t["steps"] = {"cut": {"status": "done"}, "upload": {"status": "done"}, "edit": {"status": "done"}}
    t["composition_id"] = "old-composition"
    run.request_redo("David Okafor", "end cut too early")
    check("redo request applied", run.apply_redo_requests() == ["David Okafor"] and t["steps"] == {}
          and "old-composition" in t.get("broken_compositions", []) and len(t.get("redone") or []) == 1)
    check("redo request applied only once", run.apply_redo_requests() == [])

    briefs = segment.brief(run)
    text = open(briefs[0], encoding="utf-8").read()
    check("brief written", all(x in text for x in ("## Running order", "Recording r1", "Speakers introducing themselves",
                                                    "Closing lines", "> Pod disruption budgets")))

    talk = next(t for t in events.talks(ev) if t["key"] == "Bob Martinez & Carol Chen")
    title, desc = youtube.block(ev, cfg, talk, [{"at": "0:00", "title": "Intro"}], None)
    check("YouTube title", title == "Observability Pipelines Without Tears | Bob Martinez & Carol Chen | TESTday Testcity 2026 Q4", title)
    check("YouTube description in the house style", desc[:3] == [
        "TESTday Testcity 2026 Q4 - November 5, 2026", "Grab your ticket for the next TESTday: https://www.testday.example",
        "Upcoming TESTday CFPs: https://cfp.ninja/?q=testbrand&status=open"] and desc[4] == "Chapters", desc)
    check("thumbnail URL", youtube.thumbnail_url(ev, talk).endswith("/teasers/Bob%20Martinez%20%26%20Carol%20Chen.png"))

    print("\n%s" % ("all good" if not FAILS else "%d failed: %s" % (len(FAILS), ", ".join(FAILS))))
    if not FAILS and "--keep" not in sys.argv:
        shutil.rmtree(TMP, ignore_errors=True)
    else:
        print("kept for a look: " + TMP)
    if FAILS or "--keep" in sys.argv:
        print("app on it: LIVE_FACTORY_SETTINGS=%s env/bin/python -m live_factory serve" % os.environ["LIVE_FACTORY_SETTINGS"])
    sys.exit(1 if FAILS else 0)


main()
