"""End-to-end test on difficult footage (2026-11-27, after the Fable review): one event, through the real pipeline
from the recordings to "YouTube", with stand-ins for Descript and YouTube only. Every talk must end either on YouTube
or named in the report - never a hang, a crash or a squashed picture.

  env/bin/python tests/scenarios_smoke.py          (Windows: env\\Scripts\\python.exe tests\\scenarios_smoke.py)

The recordings:
  r1  1280x720 25 fps, stereo 48 kHz            - part 1 of "Ann Lee"
  r2  1920x1080 30 fps, MONO 44.1 kHz           - part 2 of "Ann Lee" (a talk across two recordings of other sizes),
                                                  then "Łukasz Żółć" (a Polish name)
  r3  a phone video, 720x1280 portrait, rotation flag - "Ann O'Neil: Q/A?" (a name no file system takes as it is)
  r4  a recording with a damaged stretch (bytes flipped mid-file) - "Bo Chen"
  r5  no sound at all                            - "Dee Silent": must stop with a plain reason, not upload
  r6  on the lineup, not recorded                - "Zed Missing": "missing recording" in the report
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
TMP = tempfile.mkdtemp(prefix="live-factory-scenarios-")
FAILS = []


def check(what, ok, detail=""):
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what, (" - " + str(detail)) if detail else ""))
    if not ok:
        FAILS.append(what)


def ff(*args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("ffmpeg failed: " + r.stderr[-400:])


def tone(path, secs, size, fps, rate=48000, ch=2, hz=330, audio=True, gap=None):
    expr = "0.3*sin(2*PI*%d*t)" % hz + ("*(1-between(t,%d,%d))" % gap if gap else "")
    args = ["-f", "lavfi", "-i", "testsrc2=size=%s:rate=%d" % (size, fps)]
    if audio:
        args += ["-f", "lavfi", "-i", "aevalsrc=%s:s=%d:d=%d" % (expr.replace(",", "\\,"), rate, secs)]
    args += ["-t", str(secs), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    args += (["-c:a", "aac", "-ac", str(ch), "-ar", str(rate)] if audio else ["-an"])
    ff(*(args + [path]))


def build():
    ev = os.path.join(TMP, "testbrand", "2026-scenariocity-q4")
    os.makedirs(os.path.join(ev, "_db"))
    with open(os.path.join(ev, "metadata.yml"), "w", encoding="utf-8") as f:
        f.write('brand_name: "TESTday"\nbrand_domain: "testday.example"\nstart_time: "2026-11-05T09:00:00+01:00"\n'
                'city_name: "Scenariocity"\ndate_string: "November 5, 2026"\ndays: 1\nrooms:\n- main\n'
                'youtube_url: https://www.youtube.com/@testday\n')
    names = ["Ann Lee", "Łukasz Żółć", "Ann O'Neil: Q/A?", "Bo Chen", "Dee Silent", "Zed Missing"]
    with open(os.path.join(ev, "_db", "talks.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["YouTube", "status", "name", "track", "day", "organization", "title", "duration", "abstract"])
        for i, n in enumerate(names):
            w.writerow(["", "talk", n, "1", "1", "Acme", "Talk number %d about reliability" % (i + 1),
                        "30 min" if i == 1 else "2", "An abstract."])
    settings = os.path.join(TMP, "settings.scenarios.yml")
    with open(settings, "w", encoding="utf-8") as f:
        f.write("github_root: %s\nbrands:\n  testbrand: {repo: testbrand}\nwork_root: %s\nwhisper_model: tiny.en\n"
                "frame_every_seconds: 30\ntalk_min_minutes: 0.2\ntalk_max_minutes: 3\npublish_status: false\n"
                "sharp_ends: false\nzoom_black_bars: false\nproofread_claude: false\n"
                % (json.dumps(TMP), json.dumps(os.path.join(TMP, "work"))))
    card = os.path.join(TMP, "card")
    os.makedirs(card)
    tone(os.path.join(card, "r1_cam.mp4"), 45, "1280x720", 25)
    tone(os.path.join(card, "r2_backup.mp4"), 100, "1920x1080", 30, rate=44100, ch=1, hz=420)
    flat = os.path.join(TMP, "phone_flat.mp4")
    tone(flat, 45, "1280x720", 30, hz=500)            # a phone stores it landscape + a rotation flag
    ff("-display_rotation", "90", "-i", flat, "-c", "copy", os.path.join(card, "r3_phone.mp4"))
    good = os.path.join(TMP, "r4_good.mp4")
    tone(good, 50, "1280x720", 25, hz=600)
    data = bytearray(open(good, "rb").read())
    for k in range(len(data) // 2, len(data) // 2 + 60000, 7):   # a damaged stretch in the middle
        data[k] ^= 0xFF
    with open(os.path.join(card, "r4_damaged.mp4"), "wb") as f:
        f.write(bytes(data))
    tone(os.path.join(card, "r5_nosound.mp4"), 40, "1280x720", 25, audio=False)
    return settings, card


def main():
    settings, card = build()
    os.environ["LIVE_FACTORY_SETTINGS"] = settings
    import autopilot_smoke as ap
    from live_factory import analyse, media, pipeline, progress, ytupload
    fake = ap.install_fakes(TMP)
    print("scenarios end to end in " + TMP)
    run = pipeline.Run("testbrand-2026-scenariocity-q4")
    run.cfg.setdefault("youtube", {}).setdefault("channels", {})["testbrand"] = "UCtest"
    for f in sorted(os.listdir(card)):
        run.add([os.path.join(card, f)], day=1)
    for rid in run.state["recordings"]:
        run.copy_in(rid)
        analyse._write_json(os.path.join(run.rec_dir(rid), "transcript.json"), {"segments": []})
        run.rset(rid, "transcript", "done", note="not needed for this test")
    t0 = time.time()
    run.analyse()
    recs = {os.path.basename(r.get("source") or r.get("path") or ""): k for k, r in run.state["recordings"].items()}
    rid = lambda name: next(k for k, r in run.state["recordings"].items() if name in json.dumps(r))
    check("analyse: every recording done, the damaged one too",
          all(all((r["steps"].get(s) or {}).get("status") == "done" for s in pipeline.REC_STEPS)
              for r in run.state["recordings"].values()),
          {k: {s: (r["steps"].get(s) or {}).get("status") for s in pipeline.REC_STEPS}
           for k, r in run.state["recordings"].items()})
    phone = media.probe(run.rec_path(rid("r3_phone")))
    check("a rotated phone video reads as portrait", (phone["width"], phone["height"]) == (720, 1280), phone)
    cuts = {"autopilot": True, "cuts": [
        {"key": "Ann Lee", "parts": [{"rec": rid("r1_cam"), "start": 2, "end": 44},
                                     {"rec": rid("r2_backup"), "start": 1, "end": 38}], "by": "claude", "reason": "t"},
        {"key": "Łukasz Żółć", "parts": [{"rec": rid("r2_backup"), "start": 40, "end": 98}], "by": "claude", "reason": "t"},
        {"key": "Ann O'Neil: Q/A?", "parts": [{"rec": rid("r3_phone"), "start": 1, "end": 44}], "by": "claude", "reason": "t"},
        {"key": "Bo Chen", "parts": [{"rec": rid("r4_damaged"), "start": 1, "end": 49}], "by": "claude", "reason": "t",
         "check": "very likely Bo's talk, but the name was not heard"},
        {"key": "Dee Silent", "parts": [{"rec": rid("r5_nosound"), "start": 1, "end": 39}], "by": "claude", "reason": "t"}],
        "skipped": [{"key": "Zed Missing", "why": "not recorded - the room's camera was off"}]}
    run.save_cuts(cuts)
    ok, held = run.approve(["Ann Lee", "Łukasz Żółć", "Ann O'Neil: Q/A?", "Bo Chen", "Dee Silent"])
    check("all five approved", len(ok) == 5, (ok, held))
    run.run()
    took = time.time() - t0
    check("the whole run ends (no hang) - %.0f s" % took, took < 1800)
    t = run.state["talks"]
    done = ytupload.load(run)
    up = sorted(k for k in done if k != ytupload.PLAYLIST and done[k].get("id"))
    check("on YouTube: Ann (two recordings), Łukasz, the phone talk, the damaged one",
          up == sorted(["Ann Lee", "Łukasz Żółć", "Ann O'Neil: Q/A?", "Bo Chen"]), {k: (t[k].get("error") or
                                                                                       t[k].get("problems"))
                                                                                   for k in t})
    a = media.probe(run.final_path("Ann Lee"))
    check("Ann's two parts: one size, both parts, sound all through", (a["width"], a["height"]) == (1280, 720) and
          75 <= a["duration"] <= 82, a)
    p = media.probe(run.final_path("Ann O'Neil: Q/A?"))
    check("the phone talk stays portrait (not squashed)", p["height"] > p["width"], p)
    check("file names: the odd name is made safe", os.path.basename(run.final_path("Ann O'Neil: Q/A?")).startswith(
        "Ann O'Neil QA"), run.final_path("Ann O'Neil: Q/A?"))
    dee = t.get("Dee Silent") or {}
    check("no sound: not uploaded, stopped with a plain reason", "Dee Silent" not in up and
          ("no sound" in str(dee.get("error")) and "before Descript" in str(dee.get("error"))), dee.get("error"))
    pg = progress.payload(run.ev["id"], run=run)
    issues = " | ".join(pg["issues"])
    check("the progress page: missing recording, Dee needs a look, nothing about sound quality",
          "Missing recording for Zed Missing" in issues and "Dee Silent" in issues and "sound" not in issues.lower(),
          pg["issues"])
    check("the progress page: four talks done", sum(1 for r in pg["talks"] if r["state"] == "done") == 4,
          [(r["name"], r["mission"]) for r in pg["talks"]])
    check("benefit of the doubt: Bo shipped AND on the progress page to watch", "Bo Chen" in up and
          any("Please watch Bo Chen" in i for i in pg["issues"]), pg["issues"])
    rep = open(os.path.join(run.dir, "ship report.md"), encoding="utf-8").read()
    check("ship report: Bo under 'Please check' with the why", "## Please check (1)" in rep and
          "the name was not heard" in rep, rep[rep.find("## Please"):][:300])
    check("ship report: 4 on YouTube, Dee, Zed", "## On YouTube (4)" in rep and "Dee Silent" in rep and
          "Zed Missing" in rep, rep[:600])
    print("\nfiles: " + TMP)
    print("FAILED: " + ", ".join(FAILS) if FAILS else "all good")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
