"""End-to-end test of the autopilot (2026-11-27): a small event through the real pipeline, from the recording to
"YouTube", with stand-ins for Descript and YouTube only - no network, no credits.

  env/bin/python tests/autopilot_smoke.py          (Windows: env\\Scripts\\python.exe tests\\autopilot_smoke.py)

The day: Ann's talk (clean), Ben's talk (a 12 s dead stretch in the middle - the factory's own check must hold it),
and Cy on the lineup but not recorded. Then, as Claude runs it: add, analyse, cuts, autopilot on, approve, run.
Checks: gate 1 by Claude, no review waited for, every step done, selfcheck passes Ann and holds Ben, only Ann
uploaded - private, publishAt 5 weeks after the event at 09:00, title, thumbnail, captions, the playlist made and
noted for hooking - the ship report (on YouTube / problematic / missing), and a second run uploading nothing again.
"""
import json
import os
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
TMP = tempfile.mkdtemp(prefix="live-factory-autopilot-")
FAILS = []


def check(what, ok, detail=""):
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what, (" - " + str(detail)) if detail else ""))
    if not ok:
        FAILS.append(what)


def ff(*args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("ffmpeg failed: " + r.stderr[-400:])


def fixture():
    ev = os.path.join(TMP, "testbrand", "2026-testcity-q4")
    os.makedirs(os.path.join(ev, "_db"))
    with open(os.path.join(ev, "metadata.yml"), "w") as f:
        f.write('brand_name: "TESTday"\nbrand_domain: "testday.example"\nstart_time: "2026-11-05T09:00:00+00:00"\n'
                'city_name: "Testcity"\ndate_string: "November 5, 2026"\ndays: 1\nrooms:\n- main stage\n'
                'youtube_url: https://www.youtube.com/@testday\n')
    import csv
    with open(os.path.join(ev, "_db", "talks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["YouTube", "status", "name", "track", "day", "organization", "title", "duration", "abstract"])
        w.writerow(["", "keynote", "Ann Lee", "1", "1", "Acme", "Budgets Beat Alerts", "2", "SLOs."])
        w.writerow(["", "talk", "Ben Ray & Cara Fox", "1", "1", "Acme", "On-call Without Heroics", "2", "On call."])
        w.writerow(["", "talk", "Cy Doe", "1", "1", "Acme", "Kafka at the Edge", "2", "Kafka."])
    settings = os.path.join(TMP, "settings.autopilot.yml")
    with open(settings, "w") as f:
        f.write("github_root: %s\nbrands:\n  testbrand: {repo: testbrand}\nwork_root: %s\nwhisper_model: tiny.en\n"
                "frame_every_seconds: 30\ntalk_min_minutes: 0.2\ntalk_max_minutes: 3\npublish_status: false\n"
                "sharp_ends: false\nzoom_black_bars: false\n"
                % (json.dumps(TMP), json.dumps(os.path.join(TMP, "work"))))
    # the day: 5 s quiet, Ann 50 s, 10 s quiet, Ben 60 s with 12 s of dead sound in it, 5 s quiet
    card = os.path.join(TMP, "card")
    os.makedirs(card)
    audio = os.path.join(card, "day.wav")
    expr = ("0.3*sin(2*PI*330*t)*(between(t,5,55)+between(t,65,125)*(1-between(t,90,102)))")
    ff("-f", "lavfi", "-i", "aevalsrc=%s:s=48000:d=130" % expr.replace(",", "\,"), "-ac", "2", audio)
    video = os.path.join(card, "DAY1_CAM.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25", "-i", audio, "-t", "130", "-c:v", "libx264", "-preset",
       "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video)
    return settings, card


class FakeDescript:
    """The calls pipeline.process makes, answered from the files it uploads."""
    files, n = {}, 0

    def find_project(self, name):
        return getattr(FakeDescript, "pid", None)

    def cancel_orphan_uploads(self, pid):
        return []

    def project(self, pid):
        from live_factory import media
        comps = [{"id": c, "name": k, "duration": media.probe(p)["duration"]} for c, (k, p) in FakeDescript.files.items()]
        return {"compositions": comps, "media_files": {k: {"duration": d["duration"]} for k, d in
                                                       ((x["name"], x) for x in comps)}}

    def import_file(self, path, key, project_id=None, project_name=None, on_progress=None, media_name=None):
        FakeDescript.n += 1
        FakeDescript.pid = project_id or "P1"
        cid = "C%d" % FakeDescript.n
        FakeDescript.files[cid] = (key, path)
        return FakeDescript.pid, cid, None

    def edit(self, pid, cid, prompt, on_progress=None):
        return {"job_id": "J", "result": {"ai_credits_used": 9.5, "agent_response": "Studio Sound on, fillers removed."}}

    def publish(self, pid, cid, resolution, access, on_progress=None):
        return {"job_id": "J", "result": {"share_url": "https://share/%s" % cid, "download_url": "fake://%s" % cid}}

    def srt(self, pid, cid):
        from live_factory import media                  # cues at the very start and end: nothing to trim
        d = media.probe(FakeDescript.files[cid][1])["duration"]
        stamp = lambda x: "00:%02d:%02d,%03d" % (int(x) // 60, int(x) % 60, int(x * 1000) % 1000)
        return ("1\n%s --> %s\nHello everyone.\n\n2\n%s --> %s\nThank you.\n" % (
            stamp(0.3), stamp(4), stamp(d - 4), stamp(d - 0.3))).encode()

    def chapters(self, pid, cid, name, on_progress=None, title=None, title_room=None):
        return "00:00 Intro\n00:15 The middle\n00:30 Wrap up", {"ai_credits_used": 1}


class FakeGet:
    """requests.get for Descript's download link: the uploaded file itself."""
    def __init__(self, url, stream=False, timeout=None):
        self.path = FakeDescript.files[url.split("://")[1]][1]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        with open(self.path, "rb") as f:
            while True:
                b = f.read(n)
                if not b:
                    return
                yield b


def main():
    settings, card = fixture()
    os.environ["LIVE_FACTORY_SETTINGS"] = settings
    from live_factory import machine, media, pipeline, segment, ytupload
    shared = os.path.join(TMP, "local")
    os.makedirs(shared, exist_ok=True)
    machine.shared_dir = lambda cfg: shared            # the test's own ledger of paid edits, not the checkout's
    import youtube_smoke as ys
    media.notify = lambda *a: None
    pipeline.Descript = FakeDescript
    pipeline.requests = types.SimpleNamespace(get=FakeGet)
    pipeline.Run.publish = lambda self, force=False: None
    fake = ys.FakeYouTube(channel="UCtest", handle="@testday")
    ytupload.requests = types.SimpleNamespace(request=fake.request, post=fake.post, put=fake.put, get=fake.get,
                                              RequestException=Exception)
    ytupload.time.sleep = lambda s: None
    ytupload.PAUSE = lambda s: None
    ytupload.YouTube.for_brand = classmethod(lambda cls, brand: cls({"client_id": "c", "client_secret": "s",
                                                                     "refresh_token": "r", "channel_id": "UCtest"}))
    print("autopilot end to end in " + TMP)
    run = pipeline.Run("testbrand-2026-testcity-q4")
    run.cfg.setdefault("youtube", {}).setdefault("channels", {})["testbrand"] = "UCtest"
    run.add([card], day=1)
    run.copy_in("r1")
    pipeline.analyse.run_whisper = None
    run.rset("r1", "transcript", "done", note="not needed for this test")
    from live_factory import analyse
    analyse._write_json(os.path.join(run.rec_dir("r1"), "transcript.json"), {"segments": []})
    run.analyse()
    run.save_cuts({"cuts": [{"key": "Ann Lee", "parts": [{"rec": "r1", "start": 5.0, "end": 55.0}], "by": "claude",
                             "reason": "test"},
                            {"key": "Ben Ray & Cara Fox", "parts": [{"rec": "r1", "start": 65.0, "end": 125.0}],
                             "by": "claude", "reason": "test"}],
                   "skipped": [{"key": "Cy Doe", "why": "not recorded (the room mic was off)"}]})
    data = run.cuts()
    data["autopilot"] = True
    run.save_cuts(data)
    check("autopilot on (cuts.json)", run.autopilot())
    ok, held = run.approve()
    c = {x["key"]: x for x in run.cuts()["cuts"]}
    check("gate 1 by Claude: both approved, who and when kept", sorted(ok) == ["Ann Lee", "Ben Ray & Cara Fox"] and
          c["Ann Lee"].get("approved_by") == "claude" and c["Ann Lee"].get("approved_at") and not held, (ok, held))
    run.run()
    t = run.state["talks"]
    steps = lambda k: {s: (t[k]["steps"].get(s) or {}).get("status") for s in pipeline.STEPS}
    check("no review waited for, every step done (Ann)", all(v == "done" for v in steps("Ann Lee").values()) and
          t["Ann Lee"].get("finished"), steps("Ann Lee"))
    check("selfcheck passes Ann", t["Ann Lee"].get("selfcheck_ok") is True, t["Ann Lee"].get("problems"))
    check("selfcheck holds Ben (12 s of dead sound)", t["Ben Ray & Cara Fox"].get("selfcheck_ok") is False and
          any("silence" in p for p in t["Ben Ray & Cara Fox"].get("problems") or []), t["Ben Ray & Cara Fox"].get("problems"))
    done = ytupload.load(run)
    check("only Ann on YouTube", sorted(k for k in done if not k.startswith("_")) == ["Ann Lee"], sorted(done))
    rec = done.get("Ann Lee", {})
    body = (fake.videos.get(rec.get("id")) or {}).get("body") or {}
    check("private, public on Dec 10 09:00 (5 weeks after Nov 5, the event's UTC offset)",
          body.get("status", {}).get("privacyStatus") == "private" and
          body["status"].get("publishAt") == "2026-12-10T09:00:00Z", body.get("status"))
    check("title: Keynote: <title> | <speaker> | <event>", body.get("snippet", {}).get("title") ==
          "Keynote: Budgets Beat Alerts | Ann Lee | TESTday Testcity 2026 Q4", body.get("snippet", {}).get("title"))
    check("description with the chapters", "00:15 The middle" in body.get("snippet", {}).get("description", ""))
    check("thumbnail, captions, playlist", rec.get("thumbnail") is True and rec.get("captions") is True and
          rec.get("playlist") is True, rec)
    check("the new playlist noted for hooking to the website", (done.get("_playlist") or {}).get("url"))
    rep = open(os.path.join(run.dir, "ship report.md"), encoding="utf-8").read()
    check("ship report: on YouTube / problematic / missing / playlist / credits",
          "## On YouTube (1)" in rep and "## Problematic (1)" in rep and "## Missing (1)" in rep and
          "not recorded" in rep and "## Playlist" in rep and "AI credits used: 19.0" in rep, rep)
    n = len(fake.videos)
    run.run()
    check("a second run uploads nothing again", len(fake.videos) == n, len(fake.videos))
    # the day's quota stopped a talk: the next `run`, with nothing new to cut, still uploads it
    done = ytupload.load(run)
    fake.videos.pop(done["Ann Lee"]["id"], None)
    done.pop("Ann Lee")
    ytupload.save(run, done)
    run.run()
    check("a rerun with nothing to cut uploads what was left", (ytupload.load(run).get("Ann Lee") or {}).get("id"))
    print("\nfiles: " + TMP)
    print("FAILED: " + ", ".join(FAILS) if FAILS else "all good")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
