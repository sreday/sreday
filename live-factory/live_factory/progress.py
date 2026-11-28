"""The simple progress page (Marek 2026-11-27): "it opens localhost on any browser and shows progress on the files in an
easy, single-tab way ... 'cutting up conference video' - progress bar, then 'making cuts' 'fixing audio', then we split
into the list of talks with their missions ... ending with 'uploaded to youtube' and 'finished'. A dirty simple, noob
friendly dashboard that doesn't go into any details, just progress bars and then final report talking only about real
issues 'missing speaker' 'missing recording for X', not 'oh no the sound wasn't great'".

payload(event_id) -> {title, stages: [{name, percent, state}], talks: [{name, title, mission, percent, state}],
issues: [str], finished: bool, running: bool} - plain words only; the workbench (/workbench) keeps the details.
"""
import os
import re

from . import events, pipeline, ytupload

# a talk's steps -> the mission shown for it, in order
MISSIONS = [("cut", "cutting the talk out"), ("check", "checking the cut"), ("upload", "sending it to Descript"),
            ("edit", "fixing the audio and video"), ("review", "fixing the audio and video"),
            ("publish", "exporting the video"), ("download", "downloading the video"),
            ("srt", "writing the subtitles"), ("proofread", "proofreading the subtitles"),
            ("chapters", "writing the chapters"), ("loudness", "finishing the sound"), ("qa", "checking the video"),
            ("selfcheck", "checking the video")]
NO_SHOW = re.compile(r"\bno[\s-]?show\b|\bdidn'?t (show|come|turn up)|\bdid not (show|come|turn up)|\bcancell?ed\b|"
                     r"\bwithdrew\b|\bdropped out\b", re.I)


REC_GONE = re.compile(r"not found|not recorded|no sound|muted|silence|recording stops|camera (was )?off|"
                      r"no recording|wasn'?t recorded|was not recorded|\?", re.I)


def _pct(done, total):
    return int(round(100.0 * done / total)) if total else 0


def _stage(name, done, total, running=False, partial=0.0):
    pct = min(100, _pct(done + partial, total))
    state = "done" if total and done >= total else "running" if running or done or partial else "waiting"
    return {"name": name, "percent": 100 if state == "done" else pct, "state": state}


def talk_row(key, title, t, yt_rec, held, cut, steps_total, shelf=None):
    """One talk's line: its mission in plain words and how far it is."""
    if yt_rec and yt_rec.get("id"):
        when = (yt_rec.get("publish_at") or "")[:10]
        return {"mission": "on YouTube" + (" - public on %s" % when if when else ""), "percent": 100, "state": "done"}
    if held:
        return {"mission": "waiting - its start or end isn't sure yet", "percent": 0, "state": "problem"}
    if not t:
        return {"mission": "waiting for its turn" if cut and cut.get("approved") else "waiting for the cut",
                "percent": 0, "state": "waiting"}
    steps = t.get("steps") or {}
    if t.get("finished"):
        if t.get("selfcheck_ok") is False:
            return {"mission": "not uploaded - needs a look", "percent": 100, "state": "problem"}
        if shelf:
            return {"mission": "finished - in the final videos folder", "percent": 100, "state": "done"}
        return {"mission": "uploading to YouTube", "percent": 95, "state": "running"}
    if t.get("error"):
        return {"mission": "stopped - needs a look", "percent": _pct(sum(1 for s, _ in MISSIONS if
                                                                        (steps.get(s) or {}).get("status") == "done"),
                                                                    steps_total), "state": "problem"}
    # how far: the furthest step done (a talk cut before a step existed has that step missing, not pending)
    last = max([i for i, (s, _) in enumerate(MISSIONS) if (steps.get(s) or {}).get("status") == "done"], default=-1)
    done = last + 1
    mission, part = "waiting for its turn", 0.0
    if done < len(MISSIONS):
        s, words = MISSIONS[done]
        st = steps.get(s) or {}
        if s == "review" and st.get("status") == "waiting":
            return {"mission": "waiting for your review in Descript", "percent": _pct(done, steps_total + 1),
                    "state": "waiting"}
        if st.get("status") == "running":
            part = float(st.get("percent") or 0) / 100.0
        if done or st.get("status") == "running":
            mission = words
    pct = min(94, _pct(done + part, steps_total + 1))     # the upload is the last slice
    state = "running" if done or part or any((steps.get(s) or {}).get("status") == "running" for s, _ in MISSIONS) \
        else "waiting"
    return {"mission": mission, "percent": pct, "state": state}


def payload(event_id, run=None, runner=None):
    run = run or pipeline.Run(event_id)
    st, ev = run.state, run.ev
    recs = st.get("recordings") or {}
    talks_state = st.get("talks") or {}
    data = run.cuts()
    cuts = {c["key"]: c for c in data.get("cuts") or []}
    skipped = {s.get("key"): s.get("why") or s.get("reason") or "" for s in data.get("skipped") or []}
    lineup = events.talks(ev)
    yt = ytupload.load(run) if os.path.exists(ytupload.state_path(run)) else {}
    ends_flags = {}
    try:
        import json
        with open(os.path.join(run.dir, "ends.json"), encoding="utf-8") as f:
            for k, v in (json.load(f) or {}).items():
                rec = (v or {}).get("rec", v) if isinstance(v, dict) else {}
                if isinstance(rec, dict) and rec.get("flags"):
                    ends_flags[k] = True
    except (OSError, ValueError):
        pass

    def rec_done(r, step):
        return (r.get("steps", {}).get(step) or {}).get("status") == "done" or (step == "copy" and r.get("in_place"))

    def rec_part(r, step):
        s = r.get("steps", {}).get(step) or {}
        return float(s.get("percent") or 0) / 100.0 if s.get("status") == "running" else 0.0

    stages = []
    n = len(recs)
    stages.append(_stage("Copying the recordings", sum(1 for r in recs.values() if rec_done(r, "copy")), n,
                         partial=sum(rec_part(r, "copy") for r in recs.values())))
    an = [s for s in pipeline.REC_STEPS if s != "copy"]
    stages.append(_stage("Cutting up the conference video", sum(1 for r in recs.values() for s in an if rec_done(r, s)),
                         n * len(an), partial=sum(rec_part(r, s) for r in recs.values() for s in an)))
    found = sum(1 for t in lineup if t["key"] in cuts or t["key"] in skipped)
    stages.append(_stage("Finding the talks", found if cuts else 0, len(lineup)))
    approved = [k for k, c in cuts.items() if c.get("approved")]
    total = len(approved) or len(cuts)
    did = lambda step: sum(1 for k in approved if ((talks_state.get(k) or {}).get("steps", {}).get(step) or {})
                           .get("status") == "done")
    stages.append(_stage("Making the cuts", did("cut"), total))
    stages.append(_stage("Fixing the audio and video", did("edit"), total))
    stages.append(_stage("Finishing the videos", sum(1 for k in approved if (talks_state.get(k) or {}).get("finished")),
                         total))
    ok = [k for k in approved if (talks_state.get(k) or {}).get("finished") and
          (talks_state.get(k) or {}).get("selfcheck_ok") is not False]
    up = sum(1 for k in ok if (yt.get(k) or {}).get("id"))
    if not ytupload.shelved(run.cfg, ev["brand"]):            # PEC: no upload stage at all
        stages.append(_stage("Uploading to YouTube", up, len(ok) or total))

    rows = []
    for t in lineup:
        k = t["key"]
        if k in skipped and k not in cuts:
            continue                                      # in the report, not a progress line
        if k not in cuts:
            continue
        held = k in ends_flags and not cuts[k].get("approved") and not isinstance(cuts[k].get("ends"), (dict, str))
        r = talk_row(k, t["title"], talks_state.get(k), yt.get(k), held, cuts.get(k), len(MISSIONS),
                     ytupload.shelved(run.cfg, ev["brand"]))
        rows.append(dict(r, name=k, title=t["title"]))

    issues = []
    for t in lineup:
        k = t["key"]
        if k in skipped and k not in cuts:
            why = skipped[k]
            no_show = NO_SHOW.search(why or "") and not REC_GONE.search(why or "")
            issues.append(("Missing speaker: %s" if no_show else "Missing recording for %s") % k)
        elif k not in cuts and cuts:
            issues.append("Missing recording for %s" % k)
    for r in rows:
        if r["state"] == "problem":
            issues.append("%s: %s" % (r["name"], r["mission"]))
    for t in lineup:
        ts = talks_state.get(t["key"]) or {}
        if ts.get("doubts") and (yt.get(t["key"]) or {}).get("id"):
            issues.append("Please watch %s's video - shipped on the benefit of the doubt (private until its release)"
                          % t["key"])
    if cuts and not ytupload.has_token(ev["brand"]) and not ytupload.shelved(run.cfg, ev["brand"]):
        issues.append("YouTube isn't connected for %s on this computer" % ev["brand_name"])
    left = st.get("leftover_compositions") or []
    if left:
        issues.append("Delete %d leftover clip%s in Descript (named like the talks; the ship report says which)" %
                      (len(left), "" if len(left) == 1 else "s"))
    if st.get("edits_paused"):
        issues.append("Descript edits are paused - a talk needs a look in Descript")
    running = bool(runner)
    all_done = bool(rows) and all(r["state"] == "done" for r in rows)
    shelf = ytupload.shelved(run.cfg, ev["brand"])
    return {"event": event_id, "title": ev["title"], "stages": stages, "talks": rows, "issues": issues,
            "no_upload": shelf or "",
            "running": running, "job": (runner or {}).get("job") or "", "finished": all_done and not running,
            "report_ready": bool(rows) and not running and all(r["state"] in ("done", "problem") for r in rows)}
