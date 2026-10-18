"""The local LIVE talk factory app: http://localhost:8043 (start it with run.sh / run.cmd).

Per event: add recordings, watch the analysis, check and approve the cuts against the recording itself (gate 1),
start the Descript runs, tick talks Reviewed after looking at them in Descript (gate 2), open the outputs.
Every long job (analyse, run) is its own background process, so closing the browser never stops it; one job per
event at a time, because a job owns the event's state.json while it runs. The dashboard itself only writes
cuts.json and reviews.json.
"""
import datetime
import html
import json
import os
import re
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import yaml

from . import events, machine, media, pipeline, segment

PORT = 8043                      # the Conf42 factory has 8042
PY = sys.executable
HERE = pipeline.HERE
PAGE_FILE = os.path.join(os.path.dirname(__file__), "dashboard.html")


def code_stamp():
    """When the factory's code last changed: an app started before that is stale and gets replaced (2026-10-17: an app
    left running from the day before didn't know about past/ and showed the wrong event)."""
    d = os.path.dirname(__file__)
    return max(os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d) if f.endswith((".py", ".html")))


STARTED_STAMP = code_stamp()
STATUS_FILE = os.path.join(os.path.dirname(__file__), "status.html")   # /status: the read-only overview


def work_root():
    return machine.work_root(pipeline.settings())


def event_dir(event_id):
    """The event's folder, "video factory - <event>" (machine.event_dir)."""
    try:
        return machine.event_dir(pipeline.settings(), event_id)
    except (KeyError, ValueError):
        return machine.event_dirs(pipeline.settings()).get(event_id) or os.path.join(work_root(), event_id)


def state_of(event_id):
    p = os.path.join(event_dir(event_id), "state.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def event_list():
    """Events with a work folder first, then every brand's events from a year ago on (newest first)."""
    cfg = pipeline.settings()
    dirs = machine.event_dirs(cfg)                    # started events: {id: "video factory - <event>" folder}
    year_ago = str(datetime.date.today().replace(year=datetime.date.today().year - 1))
    out = []
    for eid, path in events.all_events(cfg):
        started = eid in dirs
        folder = os.path.basename(path)
        if started or folder[:4] >= year_ago[:4]:
            out.append({"id": eid, "started": started, "folder": folder})
    # the event worked on last comes first: it is the one the app opens on
    for x in out:
        x["active"] = os.path.getmtime(os.path.join(dirs[x["id"]], "state.json")) if x["started"] else 0
    return sorted(out, key=lambda x: (not x["started"], -x["active"], x["folder"][:7] > str(datetime.date.today())[:7],
                                      x["folder"][:4], x["id"]))


def runner(event_id):
    """The background job working on this event, if one is alive (it saves a heartbeat every 30 s)."""
    st = state_of(event_id) or {}
    r = st.get("runner") or {}
    try:
        age = (datetime.datetime.now() - datetime.datetime.fromisoformat(r.get("at"))).total_seconds()
    except (TypeError, ValueError):
        return None
    if not r.get("pid") or age > 120:
        return None
    try:
        os.kill(r["pid"], 0)
    except (OSError, SystemError):
        return None
    except Exception:
        pass
    return {"pid": r["pid"], "job": st.get("job") or "", "since": st.get("run_started") or r.get("at")}


def spawn(args, event_id):
    os.makedirs(event_dir(event_id), exist_ok=True)
    log = open(os.path.join(event_dir(event_id), "run.log"), "a", encoding="utf-8")
    log.write("\n==== %s  %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), " ".join(args)))
    log.flush()
    extra = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([PY, "-m", "live_factory"] + args, cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                     env={**os.environ, "PYTHONUTF8": "1"}, **extra)


_levels_cache, _annot_cache = {}, {}


def levels_of(run, rid):
    """levels.json, cached by modification time (the cut list asks for it on every refresh)."""
    path = os.path.join(run.rec_dir(rid), "levels.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = _levels_cache.get(path)
    if not hit or hit[0] != mt:
        with open(path, encoding="utf-8") as f:
            hit = _levels_cache[path] = (mt, json.load(f))
    return hit[1]


def credits_of(run):
    """Descript AI credits this event's edits have cost so far (the ledger of paid edits)."""
    led = run.ledger()
    mine = [v for v in led.values() if v.get("event") == run.ev["id"]]
    return {"edits": len(mine), "credits": round(sum(float(v.get("credits") or 0) for v in mine), 1)}


def event_payload(event_id):
    run = pipeline.Run(event_id)
    sch = events.schedule(run.ev)
    data = run.cuts()
    problems = segment.validate(run, data)
    key = (run.cuts_path, json.dumps(data, sort_keys=True), os.path.getmtime(run.state_path) if os.path.exists(run.state_path) else 0)
    if _annot_cache.get("key") != key:               # assess() reads the transcripts: only when something changed
        _annot_cache.update(key=key, data=segment.annotate(run, json.loads(json.dumps(data))))
    data = json.loads(json.dumps(_annot_cache["data"]))
    from . import stereo
    for c in data["cuts"]:
        verdict = None
        for p in c["parts"]:
            lv = levels_of(run, p["rec"])
            if lv:
                v = stereo.check(lv, p["start"], p["end"], run.cfg)
                if verdict is None or (v["fix"] and not verdict["fix"]):
                    verdict = v
        c["stereo"] = verdict
    descript = ("https://web.descript.com/" + run.state["project_id"]) if run.state.get("project_id") else ""
    free = None
    try:
        import shutil
        free = round(shutil.disk_usage(run.dir).free / 1e9, 1)
    except OSError:
        pass
    return {"event": run.ev, "state": run.state, "disk": {"free_gb": free, "need_gb": round(run.space_needed() / 1e9, 1)}, "summary": pipeline.summary(run.state), "cuts": data,
            "problems": problems, "reviews": run.reviews(), "redo": run.redo_requests(), "runner": runner(event_id), "descript_url": descript,
            "schedule": {k: {"day": v["day"], "room": v["room"], "date": v["date"], "items": v["items"]}
                         for k, v in sch.items()},
            "talks": [{"key": t["key"], "title": t["title"], "track": t["track"], "kind": t["kind"]} for t in events.talks(run.ev)],
            "steps": pipeline.STEPS, "rec_steps": pipeline.REC_STEPS,
            "log": (run.state.get("log") or [])[-40:], "credits": credits_of(run)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path, ctype):
        """A file with HTTP range support, so the browser's <video> can seek in an 8 h recording."""
        size = os.path.getsize(path)
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        start, end = 0, size - 1
        if rng:
            if rng.group(1):
                start = int(rng.group(1))
                end = int(rng.group(2)) if rng.group(2) else size - 1
            else:
                start = size - int(rng.group(2))
        end = min(end, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass                                   # the player jumped elsewhere

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        e = q.get("e", "")
        try:
            if u.path == "/":
                with open(PAGE_FILE, encoding="utf-8") as f:
                    return self.send(200, f.read(), "text/html")
            if u.path == "/status":
                with open(STATUS_FILE, encoding="utf-8") as f:
                    return self.send(200, f.read(), "text/html")
            if u.path == "/api/version":
                return self.send(200, {"stamp": STARTED_STAMP, "pid": os.getpid()})
            if u.path == "/api/events":
                return self.send(200, event_list())
            if u.path == "/api/event":
                return self.send(200, event_payload(e))
            if u.path == "/api/doctor":
                return self.send(200, {"machine": machine.machine_name(pipeline.settings()),
                                       "checks": machine.doctor(pipeline.settings(), check_descript=False)})
            if u.path == "/api/levels":
                run = pipeline.Run(e)
                return self.send(200, levels_of(run, q["r"]) or {})
            if u.path == "/api/transcript":
                run = pipeline.Run(e)
                from . import analyse
                tj = analyse.load(run.rec_dir(q["r"]), "transcript.json") or {"segments": []}
                a, b = float(q.get("a", 0)), float(q.get("b", 1e9))
                return self.send(200, [s for s in tj["segments"] if s["end"] >= a and s["start"] <= b])
            if u.path == "/media":
                run = pipeline.Run(e)
                path = run.media_for_browser(q["r"])
                ext = os.path.splitext(path)[1].lower()
                return self.send_file(path, "video/quicktime" if ext == ".mov" else "video/mp4")
            if u.path == "/frame":
                run = pipeline.Run(e)
                sec = int(float(q["t"]))
                path = os.path.join(run.rec_dir(q["r"]), "frames", "at_%d.jpg" % sec)
                if not os.path.exists(path):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    media.still_at(run.rec_path(q["r"]), sec, path, width=480)
                with open(path, "rb") as f:
                    return self.send(200, f.read(), "image/jpeg")
            if u.path == "/api/settings":
                with open(os.path.join(HERE, "settings.yml"), encoding="utf-8") as f:
                    return self.send(200, {"yaml": f.read()})
        except Exception as ex:
            return self.send(500, {"msg": "error: %s" % ex})
        self.send(404, {"msg": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        e = body.get("e", "")
        try:
            busy = runner(e) if e else None
            if self.path == "/api/quit":           # a newer copy of the app is taking over the port
                import threading
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self.send(200, {"msg": "bye"})
            if self.path == "/api/cuts":
                problems = pipeline.Run(e).save_cuts(body["data"])
                return self.send(200, {"msg": "cuts saved" + (" - %d note(s)" % len(problems) if problems else ""),
                                       "problems": problems})
            if self.path == "/api/redo":
                pipeline.Run(e).request_redo(body["key"], body.get("why", ""), bool(body.get("on", True)))
                return self.send(200, {"msg": ("flagged %s - press Run to cut it again and re-upload it" % body["key"])
                                       if body.get("on", True) else "redo cancelled"})
            if self.path == "/api/reviewed":
                pipeline.Run(e).set_reviewed(body["key"], bool(body.get("on")))
                return self.send(200, {"msg": ""})
            if self.path == "/api/job":
                job = body["job"]
                if busy:
                    return self.send(200, {"msg": "a job is already running on this event (pid %s) - wait for it, "
                                                  "or stop it first" % busy["pid"]})
                if job == "add":
                    paths = [p.strip().strip('"').strip("'") for p in str(body.get("paths", "")).splitlines() if p.strip()]
                    args = ["add", e] + paths
                    if body.get("day"):
                        args += ["--day", str(body["day"])]
                    if body.get("track"):
                        args += ["--track", str(body["track"])]
                    if body.get("in_place"):
                        args.append("--in-place")
                    r = subprocess.run([PY, "-m", "live_factory"] + args, cwd=HERE, capture_output=True, text=True,
                                       env={**os.environ, "PYTHONUTF8": "1"}, timeout=300)
                    out = (r.stdout + r.stderr).strip().splitlines()
                    return self.send(200, {"msg": out[-1] if out else "done"})
                if job in ("analyse", "run", "draft", "brief", "youtube"):
                    if job == "run" and not any(c.get("approved") for c in pipeline.Run(e).cuts()["cuts"]):
                        return self.send(200, {"msg": "approve some cuts first"})
                    spawn([job, e], e)
                    return self.send(200, {"msg": "%s started" % job})
                if job == "retry":
                    spawn(["retry", e, body["key"]], e)
                    return self.send(200, {"msg": "retrying " + body["key"]})
                return self.send(200, {"msg": "unknown job"})
            if self.path == "/api/publish":
                from . import publish
                return self.send(200, {"msg": publish.publish()})
            if self.path == "/api/open":
                root = event_dir(e)
                target = {"folder": root, "final": machine.final_folder(pipeline.settings(), root),
                          "youtube": os.path.join(root, "YouTube items.txt"), "log": os.path.join(root, "run.log")
                          }.get(body.get("what"), root)
                if body.get("what") in ("youtube", "log") and not os.path.exists(target):
                    return self.send(200, {"msg": "not there yet"})
                machine.open_folder(target)
                return self.send(200, {"msg": ""})
            if self.path == "/api/settings":
                yaml.safe_load(body["yaml"])
                with open(os.path.join(HERE, "settings.yml"), "w", encoding="utf-8") as f:
                    f.write(body["yaml"])
                return self.send(200, {"msg": "settings saved - the next job uses them"})
        except Exception as ex:
            return self.send(200, {"msg": "error: %s" % html.escape(str(ex))})
        self.send(404, {"msg": "not found"})


def _ask(method, path):
    from urllib.request import Request, urlopen
    with urlopen(Request("http://127.0.0.1:%d%s" % (PORT, path), data=b"{}" if method == "POST" else None,
                         method=method, headers={"Content-Type": "application/json"}), timeout=3) as r:
        return json.loads(r.read() or b"{}")


def _port_busy():
    import socket
    probe = socket.socket()
    probe.settimeout(1)
    busy = probe.connect_ex(("127.0.0.1", PORT)) == 0
    probe.close()
    return busy


def serve(event_id=None):
    """The app, opened in the browser on event_id (or the event worked on last). An app already running on the port is
    reused when it has today's code and replaced when it's older; background jobs run in their own processes and
    carry on either way."""
    import time
    url = "http://localhost:%d/" % PORT + ("?event=" + event_id if event_id else "")
    if _port_busy():
        try:
            stale = _ask("GET", "/api/version").get("stamp", 0) < code_stamp()
        except Exception:
            stale = True                       # an app from before /api/version existed
        if not stale:
            print("the LIVE talk factory app is already running on " + url)
            webbrowser.open(url)
            return
        try:
            _ask("POST", "/api/quit")
        except Exception:
            pass
        for _ in range(20):
            if not _port_busy():
                break
            time.sleep(0.25)
        if _port_busy():
            print("an older LIVE talk factory app is still running on port %d and won't stop - close its window "
                  "(or end its python process), then start this again" % PORT)
            return
        print("replaced the older app that was running")
    ThreadingHTTPServer.allow_reuse_address = os.name != "nt"
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("LIVE talk factory app on " + url)
    webbrowser.open(url)
    srv.serve_forever()
