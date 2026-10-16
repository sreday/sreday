"""The "Factory" tab of sreday.com/factory (hidden): what the LIVE talk factory has done, published from the computer
that runs it - usually a run Claude drives on Marek's Mac (Marek 2026-10-16: "show completed jobs as they complete").

publish() writes live-factory/status.json in the sreday repo - talk names, steps and results only: no links, no file
paths, no Descript ids, no tokens (the repo is public) - commits that one file and pushes it; the site build renders it
(home/_build/generate.py, FACTORY MANUAL block). Events from other computers already in the file are kept; this
computer's events replace their own entries. A computer that can't push still writes the file locally.

Runs publish on their own: when a talk is ready for review or finished (at most every status_publish_minutes) and at
the end of every job. By hand: the workbench's "Publish status" button, or `python -m live_factory publish`.
"""
import datetime
import json
import os
import shutil
import subprocess

from . import events, machine, pipeline

STATUS = os.path.join(events.HERE, "status.json")
STEPS = pipeline.STEPS
EXPORT = ["publish", "download", "srt", "chapters", "loudness", "qa"]


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def event_summary(event_id):
    """One event as the page shows it - sanitised: names, steps, results."""
    run = pipeline.Run(event_id)
    st, cuts = run.state, run.cuts()
    plan = {c["key"]: sum(p["end"] - p["start"] for p in c["parts"]) for c in cuts["cuts"] if c.get("approved")}
    redo = run.redo_requests()
    talks = []
    for key in sorted(plan, key=lambda k: min(p["start"] for c in cuts["cuts"] if c["key"] == k for p in c["parts"])):
        t = st.get("talks", {}).get(key) or {"steps": {}}
        s = {k: (t["steps"].get(k) or {}).get("status", "") for k in STEPS}
        export = "done" if s["qa"] == "done" else "running" if any(s[k] in ("running", "waiting") for k in EXPORT) \
            else "failed" if any(s[k] == "failed" for k in EXPORT) else ""
        qa = t["steps"].get("qa") or {}
        rq = redo.get(key)
        talks.append({"talk": key, "title": t.get("title") or "",
                      "cut": s["cut"], "descript": "done" if s["edit"] == "done" else s["upload"] or s["edit"],
                      "review": s["review"], "export": export, "finished": bool(t.get("finished")),
                      "error": bool(t.get("error")), "minutes": round((qa.get("minutes_after") or plan[key] / 60), 1),
                      "flags": qa.get("flags") or [],
                      "redo": bool(rq and t.get("redo_handled") != rq.get("at"))})
    recs = st.get("recordings", {})
    hours = sum((r.get("probe") or {}).get("duration") or 0 for r in recs.values()) / 3600
    analysed = sum(1 for r in recs.values() if all((r["steps"].get(x) or {}).get("status") == "done"
                                                   for x in pipeline.REC_STEPS if x != "copy" or not r.get("in_place")))
    r = st.get("runner") or {}
    return {"id": event_id, "title": run.ev["title"], "date": run.ev.get("date_string") or run.ev["date"],
            "updated": st.get("updated") or "", "machine": machine.machine_name(run.cfg),
            "running": bool(r.get("pid")), "job": st.get("job") or "",
            "recordings": len(recs), "analysed": analysed, "hours": round(hours, 1),
            "talk_hours": round(sum(plan.values()) / 3600, 1),
            "not_recorded": [{"talk": s["key"], "why": s.get("why", "")} for s in cuts.get("skipped", [])],
            "cuts": len(cuts["cuts"]), "approved": len(plan), "talks": talks,
            "finished": sum(1 for t in talks if t["finished"]),
            "review": sum(1 for t in talks if t["review"] == "waiting")}


def build():
    """status.json content: this computer's events, plus other computers' entries already in the file."""
    root = machine.work_root(pipeline.settings())
    mine = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if os.path.exists(os.path.join(root, name, "state.json")):
            try:
                mine.append(event_summary(name))
            except Exception as ex:                                  # an event of a brand repo not on this machine
                print("publish: %s skipped (%s)" % (name, ex))
    try:
        with open(STATUS, encoding="utf-8") as f:
            old = json.load(f).get("events", [])
    except (OSError, ValueError):
        old = []
    ids = {e["id"] for e in mine}
    events_ = mine + [e for e in old if e["id"] not in ids]
    events_.sort(key=lambda e: e.get("updated") or "", reverse=True)
    return {"updated": now(), "events": events_}


def _git(*args):
    exe = shutil.which("git") or (r"C:\Program Files\Git\cmd\git.exe" if os.name == "nt" else "git")
    return subprocess.run([exe, "-C", events.REPO] + list(args), capture_output=True, text=True, timeout=180)


def publish(push=True):
    """Write status.json and (push=True) commit just that file and push it. Returns what happened, never raises."""
    try:
        data = build()
        tmp = STATUS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, STATUS)
        if not push:
            return "written (not pushed)"
        rel = os.path.relpath(STATUS, events.REPO)
        _git("add", rel)
        if _git("diff", "--cached", "--quiet", "--", rel).returncode == 0:
            return "nothing changed"
        r = _git("commit", "-q", "-m", "LIVE factory status update", "--", rel)
        if r.returncode != 0:
            return "commit failed: " + (r.stderr or r.stdout)[-200:]
        _git("pull", "--rebase", "--autostash", "-q")
        r = _git("push", "-q")
        return "published - sreday.com/factory updates in a few minutes" if r.returncode == 0 else \
            "committed, push failed (sign in to GitHub on this computer): " + r.stderr[-200:]
    except Exception as ex:                                            # publishing must never break a run
        return "not published: %s" % ex
