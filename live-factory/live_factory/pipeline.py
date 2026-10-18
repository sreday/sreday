"""The LIVE talk factory pipeline: long live recordings -> one clean video per talk, Descript only for the talks.

  add       copy the recordings in (from an SD card or a disk), each tied to a day + track of the schedule
  analyse   per recording, locally and for free: channel levels, Whisper transcript, frames, a browser proxy
  segment   Claude matches the transcript to the lineup -> cuts.json (segment.py; `draft` makes a first guess)
  GATE 1    Marek checks and approves the cuts in the dashboard - nothing reaches Descript before that
  per talk: cut (ffmpeg, one-sided stereo filled) -> upload -> edit (Studio Sound, fillers, gaps)
  GATE 2    Marek reviews each talk in Descript and ticks it Reviewed
  per talk: publish -> download -> srt -> chapters -> loudness (+ trim to the speech) -> qa
  then      YouTube items.txt: titles, descriptions, chapters, thumbnail links

One state file per event (<work root>/<event>/state.json) records every step of every recording and talk, so a run
can be stopped and restarted any time: finished steps are skipped. The Descript part follows the Conf42 factory
(conf42/factory/factory/pipeline.py) - its credit guards, ledger and retries.
"""
import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests
import yaml

from . import analyse, events, machine, media, segment, stereo, youtube
from .descript import Descript

HERE = events.HERE
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".mts", ".m2ts", ".avi", ".webm", ".mxf")
REC_STEPS = ["copy", "levels", "transcript", "frames", "proxy"]
STEPS = ["cut", "upload", "edit", "review", "publish", "download", "srt", "chapters", "loudness", "qa"]
MIN_TALK_SECONDS = 30


class SpaceError(RuntimeError):
    """Not enough free disk for the job: nothing was started (Marek gets a notification)."""


def settings():
    """settings.yml (shared, in git) overlaid with settings.local.yml (this machine only, git-ignored)."""
    with open(os.path.join(HERE, "settings.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # LIVE_FACTORY_SETTINGS: one more overlay (the smoke test points the factory at its fixture event this way)
    for local in (os.path.join(HERE, "settings.local.yml"), os.environ.get("LIVE_FACTORY_SETTINGS") or ""):
        if local and os.path.exists(local):
            with open(local, encoding="utf-8") as f:
                cfg.update(yaml.safe_load(f) or {})
    return cfg


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def signature(parts):
    return hashlib.sha1(json.dumps([[p["rec"], round(p["start"], 2), round(p["end"], 2)] for p in parts]).encode()).hexdigest()[:12]


class Run:
    def __init__(self, event_id):
        self.cfg = settings()
        self.ev = events.event(self.cfg, event_id)
        self.dir = machine.event_dir(self.cfg, self.ev["id"], self.ev["title"])
        self.inp, self.cut_dir, self.out, self.srt_dir = (os.path.join(self.dir, d) for d in ("in", "cuts", "out", "srt"))
        self.final_dir = machine.final_folder(self.cfg, self.dir)
        if os.path.isdir(self.dir):                   # just looking at an event in the app makes no folder for it
            self.ensure_dirs()
        self.state_path = os.path.join(self.dir, "state.json")
        self.cuts_path = os.path.join(self.dir, "cuts.json")         # gate 1: written by the dashboard and Claude
        self.reviews_path = os.path.join(self.dir, "reviews.json")   # gate 2: written by the dashboard (never by a run,
                                                                     # so a run never overwrites a tick)
        self.lock = threading.RLock()
        self.plock = threading.Lock()                 # only one talk creates the Descript project
        self.render_sem = threading.Semaphore(max(1, int(self.cfg.get("render_parallel") or 2)))
        self._saved = 0
        self.state = self._load()
        media.protect(self.inp)                       # the raw copies, and every original, are never written or deleted
        for r in self.state["recordings"].values():
            for p in (r.get("origin"), r.get("path")):
                if p:
                    media.protect(p)

    # ---- state ---------------------------------------------------------------------------
    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        return {"event": {"id": self.ev["id"], "title": self.ev["title"]}, "project_id": None, "recordings": {},
                "talks": {}, "log": []}

    def ensure_dirs(self):
        """The event's folder "video factory - <event>" and its sub-folders, made on the first write."""
        for d in (self.inp, self.cut_dir, self.out, self.srt_dir, self.final_dir):
            os.makedirs(d, exist_ok=True)
        media.protect(self.inp)                       # again, now that in/ exists (realpath can differ once it does)

    def save(self, throttle=False):
        """Write state.json (atomically). throttle=True (progress ticks) writes at most every 2 seconds."""
        self.ensure_dirs()
        with self.lock:
            if throttle and time.time() - self._saved < 2:
                return
            self._saved = time.time()
            self.state["updated"] = now()
            self.state["runner"] = {"pid": os.getpid(), "at": now(), "name": machine.machine_name(self.cfg)}
            tmp = self.state_path + ".%d.tmp" % threading.get_ident()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.state_path)

    @contextlib.contextmanager
    def job(self, name):
        """A background job owns state.json while it runs: its name and a heartbeat every 30 s tell the dashboard
        (which then starts no second job on this event)."""
        with self.lock:
            self.state["job"], self.state["run_started"] = name, now()
            self.save()
        stop = threading.Event()
        def beat():
            while not stop.wait(30):
                self.save()
        threading.Thread(target=beat, daemon=True).start()
        try:
            yield
        finally:
            stop.set()
            with self.lock:
                self.state["runner"], self.state["job"] = {"pid": None, "at": now(), "finished": True}, None
                tmp = self.state_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, indent=1, ensure_ascii=False)
                os.replace(tmp, self.state_path)
            self.publish(force=True)

    def publish(self, force=False):
        """Push this computer's progress to the Factory tab of sreday.com/factory (publish.py): at the end of every
        job, and when a talk is ready for review or finished - then at most every status_publish_minutes."""
        if not self.cfg.get("publish_status", True):
            return
        every = float(self.cfg.get("status_publish_minutes", 20)) * 60
        if not force and time.time() - getattr(self, "_published", 0) < every:
            return
        self._published = time.time()
        from . import publish
        self.note("status page: %s" % publish.publish())

    def note(self, msg):
        with self.lock:
            self.state["log"] = (self.state["log"] + ["%s  %s" % (now(), msg)])[-300:]
            print(msg, flush=True)
            self.save()

    def rec_dir(self, rid):
        d = os.path.join(self.dir, "rec", rid)
        os.makedirs(d, exist_ok=True)
        return d

    def _step(self, steps, step, status, **extra):
        prev = steps.get(step) or {}
        e = {"status": status, "at": now(), **{k: v for k, v in extra.items() if v is not None}}
        if status in ("running", "waiting"):
            e["started"] = prev.get("started") if prev.get("status") == status and prev.get("started") else now()
        if status == "done" and prev.get("started"):
            e["took"] = round((datetime.datetime.now() - datetime.datetime.fromisoformat(prev["started"])).total_seconds())
        steps[step] = e

    def rset(self, rid, step, status, **extra):
        with self.lock:
            r = self.state["recordings"][rid]
            self._step(r["steps"], step, status, **extra)
            r["current"] = step if status == "running" else None
            self.save(throttle=status == "running" and "percent" in extra)

    def set(self, key, step, status, **extra):
        """Talk step states for the dashboard: running (label, percent), waiting (Descript's line, or Marek's review),
        done (took = seconds), failed."""
        with self.lock:
            t = self.state["talks"][key]
            if status == "running" and str(extra.get("label") or "").startswith("Descript is busy"):
                status = "waiting"
            self._step(t["steps"], step, status, **extra)
            t["current"] = step if status in ("running", "waiting") else t.get("current")
            self.save(throttle=status == "running" and "percent" in extra)

    # ---- the ledger of paid Descript edits, shared by all events (<work root>/ledger.json) ----------------------
    def ledger_path(self):
        return os.path.join(machine.shared_dir(self.cfg), "ledger.json")

    def ledger(self):
        try:
            with open(self.ledger_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def record_edit(self, composition_id, key, credits):
        with self.lock:
            led = self.ledger()
            led[composition_id] = {"event": self.ev["id"], "talk": key, "credits": credits, "at": now()}
            with open(self.ledger_path(), "w", encoding="utf-8") as f:
                json.dump(led, f, indent=1, ensure_ascii=False)

    # ---- 1. add: recordings in, each tied to a day + track ------------------------------------------------------
    def add(self, paths, day=None, track=None, in_place=False):
        """Register recordings (files, or folders of them). Copied into <event>/in/ unless in_place (then read where
        they are - an external SSD that stays plugged in). The source is never changed or deleted. Returns the ids."""
        files = []
        for p in paths:
            p = os.path.abspath(os.path.expanduser(p))
            if os.path.isdir(p):
                files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(VIDEO_EXT)
                                and not f.startswith("."))
            elif p.lower().endswith(VIDEO_EXT):
                files.append(p)
            else:
                raise ValueError("%s is not a video (%s)" % (p, ", ".join(VIDEO_EXT)))
        if not files:
            raise ValueError("no videos in %s" % ", ".join(paths))
        tr = events.track_for(self.ev, track, day)
        sch = events.schedule(self.ev)[tr]
        new = [f for f in files if not any(r["file"] == os.path.basename(f) and r.get("size") == os.path.getsize(f)
                                           for r in self.state["recordings"].values())]
        for f in new:
            if os.path.normcase(os.path.realpath(f)).startswith(os.path.normcase(os.path.realpath(self.dir)) + os.sep):
                raise ValueError("%s is inside the factory's own work folder - add the original recording" % f)
        # the whole job for these recordings has to fit on the disk, or none of it starts
        extra = []
        for f in new:
            try:
                dur = media.probe(f)["duration"]
            except Exception:
                dur = 0
            extra.append({"size": os.path.getsize(f), "duration": dur, "in_place": bool(in_place)})
        self.ensure_space("adding %d recording(s)" % len(new), extra)
        added = []
        for src in files:
            size = os.path.getsize(src)
            same = [rid for rid, r in self.state["recordings"].items()
                    if r["file"] == os.path.basename(src) and r.get("size") == size]
            if same:
                self.note("%s is already here as %s" % (os.path.basename(src), same[0]))
                continue
            media.protect(src)
            with self.lock:
                n = 1 + max([int(k[1:]) for k in self.state["recordings"] if k[1:].isdigit()] or [0])
                rid = "r%d" % n
                self.state["recordings"][rid] = {"file": os.path.basename(src), "origin": src, "size": size,
                                                 "track": tr, "day": sch["day"], "room": sch["room"],
                                                 "in_place": bool(in_place), "path": src if in_place else None,
                                                 "steps": {}, "added": now()}
                self.save()
            added.append(rid)
            self.note("%s: %s - day %d, track %s (%s)%s" % (
                rid, os.path.basename(src), sch["day"], tr, sch["room"],
                " - read in place: that file is the ONLY copy, back it up" if in_place else ""))
        return added

    def rec_path(self, rid):
        r = self.state["recordings"][rid]
        return r["path"] if r.get("in_place") else os.path.join(self.inp, "%s %s" % (rid, r["file"]))

    def copy_in(self, rid):
        """Copy a recording in - the backup of the raw file, kept for good: via .part (a pulled card never leaves a
        file that looks whole), checked against the original (size + sampled hash), then made read-only. The
        original is only ever read."""
        r = self.state["recordings"][rid]
        if r.get("in_place") or (r["steps"].get("copy") or {}).get("status") == "done":
            return
        dst = self.rec_path(rid)
        if not os.path.exists(r["origin"]):
            raise RuntimeError("%s is gone (card or disk unplugged?)" % r["origin"])
        want = media.sampled_hash(r["origin"])
        if os.path.exists(dst):                       # copied before (state lost?): keep it if it is the same file
            if media.sampled_hash(dst) != want:
                raise RuntimeError("%s already exists and differs from %s - look at both; nothing was overwritten"
                                   % (dst, r["origin"]))
        else:
            got, size, part = 0, r["size"], dst + ".part"
            with open(r["origin"], "rb") as a, open(part, "wb") as b:
                while True:
                    chunk = a.read(16 << 20)
                    if not chunk:
                        break
                    b.write(chunk)
                    got += len(chunk)
                    if got % (1 << 30) < (16 << 20) and shutil.disk_usage(self.dir).free < 2e9:
                        b.close()
                        os.remove(part)
                        self.space_alarm("the disk filled up while copying %s - the copy was removed, the original "
                                         "is untouched" % r["file"])
                    self.rset(rid, "copy", "running", label="copying %d%% of %.1f GB" % (got * 100 // size, size / 1e9),
                              percent=got * 100 // size)
            if got != size or media.sampled_hash(part) != want:
                os.remove(part)
                raise RuntimeError("%s: the copy does not match the original (%d of %d bytes) - copy again"
                                   % (r["file"], got, size))
            os.replace(part, dst)
        try:
            os.chmod(dst, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)     # read-only: nothing edits the backup
        except OSError:
            pass
        with self.lock:
            r["hash"] = want
        self.rset(rid, "copy", "done", note="%.1f GB, checked against the original, read-only" % (r["size"] / 1e9))

    # ---- disk space ------------------------------------------------------------------------------------------------
    def space_needed(self, extra=None):
        """Bytes the rest of the work needs on the work disk: copies not made yet, Whisper's audio chunk, review
        copies, the cut talks, Descript's downloads and the finished videos. Before the cuts exist, talks are taken
        as 85% of each recording. extra = [{"size", "duration", "in_place"}] for recordings about to be added."""
        mb = 1e6
        recs = list(self.state["recordings"].values())
        need, talk_secs, rate = 0.0, 0.0, []
        for r in recs + (extra or []):
            steps = r.get("steps") or {}
            done = lambda s: (steps.get(s) or {}).get("status") == "done"
            dur = (r.get("probe") or {}).get("duration") or r.get("duration") or 0
            if not r.get("in_place") and not done("copy"):
                need += r["size"]
            if not done("transcript"):
                need += 120 * mb
            if not done("proxy"):
                need += dur * 0.15 * mb if (r.get("probe") and not media.browser_playable(r["probe"], r.get("origin") or "")) else 0
            if dur:
                rate.append(min(r["size"] / dur, 1.25 * mb))   # the cut is a fresh encode: at most ~10 Mbps
            talk_secs += dur * 0.85
        approved = [c for c in self.cuts()["cuts"] if c.get("approved")]
        if approved and not extra:
            talk_secs = 0.0
            for c in approved:
                t = self.state["talks"].get(c["key"]) or {}
                if not t.get("finished"):
                    talk_secs += sum(p["end"] - p["start"] for p in c["parts"])
        cut_rate = (sum(rate) / len(rate)) if rate else 1.0 * mb
        need += talk_secs * (cut_rate + 1.0 * mb + 0.4 * mb)    # cut + Descript's export + the lean final
        return need

    def space_alarm(self, msg):
        with self.lock:
            self.state["space_warning"] = {"at": now(), "msg": msg}
            self.save()
        self.note("NOT ENOUGH SPACE: " + msg)
        media.notify("LIVE talk factory - disk full", msg)
        raise SpaceError(msg)

    def ensure_space(self, what, extra=None):
        """Stop before a job that would fill the disk - and tell Marek."""
        where = self.dir
        while not os.path.isdir(where):              # the event's folder isn't made until something is written
            where = os.path.dirname(where)
        free = shutil.disk_usage(where).free
        reserve = float(self.cfg.get("disk_reserve_gb", 10)) * 1e9
        need = self.space_needed(extra)
        if need + reserve > free:
            self.space_alarm("%s for %s needs about %.0f GB on %s, %.0f GB is free (keeping %.0f GB spare) - free "
                             "%.0f GB (ask Claude to clean up junk and cache files), or set work_root to a bigger disk in "
                             "settings.local.yml" % (
                                 what, self.ev["id"], need / 1e9, self.dir, free / 1e9, reserve / 1e9,
                                 (need + reserve - free) / 1e9))
        if self.state.get("space_warning"):
            with self.lock:
                self.state.pop("space_warning", None)
                self.save()
        return need, free

    # ---- 2. analyse --------------------------------------------------------------------------------------------
    def analyse(self, rids=None):
        """Every part of the local pass for the given (default: all) recordings, one recording at a time - it keeps
        the machine responsive (Whisper takes the CPU)."""
        self.ensure_space("the analysis and the rest of the job")
        for rid in rids or sorted(self.state["recordings"], key=lambda k: int(k[1:])):
            try:
                self.analyse_one(rid)
            except Exception as ex:
                r = self.state["recordings"][rid]
                cur = r.get("current") or "?"
                self.rset(rid, cur, "failed", error=str(ex)[:400])
                self.note("FAILED %s at %s: %s" % (rid, cur, ex))
                traceback.print_exc()

    def analyse_one(self, rid):
        r, cfg = self.state["recordings"][rid], self.cfg
        done = lambda s: (r["steps"].get(s) or {}).get("status") == "done"
        prog = lambda step: (lambda label, pct=None: self.rset(rid, step, "running", label=label, percent=pct))
        self.copy_in(rid)
        src, folder = self.rec_path(rid), self.rec_dir(rid)
        if "probe" not in r:
            with self.lock:
                r["probe"] = media.probe(src)
                self.save()
        dur = r["probe"]["duration"]
        if not done("levels"):
            self.rset(rid, "levels", "running", label="reading the audio levels", percent=0)
            lv = media.levels(src, dur, prog("levels"))
            analyse._write_json(os.path.join(folder, "levels.json"), lv)
            whole = stereo.check(lv, 0, len(lv["left"]), cfg)
            self.rset(rid, "levels", "done", stereo=whole["fix"], note=whole["note"])
        if not done("transcript"):
            self.rset(rid, "transcript", "running", label="loading Whisper", percent=0)
            segs = analyse.transcribe(src, folder, dur, cfg, prog("transcript"))
            words = sum(len(s["text"].split()) for s in segs)
            self.rset(rid, "transcript", "done", note="%d segments, %d words, model %s" % (
                len(segs), words, "/".join(machine.whisper_setup(cfg)[:2])))
        if not done("frames"):
            self.rset(rid, "frames", "running", label="taking stills")
            got = media.stills(src, os.path.join(folder, "frames"), int(cfg.get("frame_every_seconds") or 120))
            self.rset(rid, "frames", "done", note="%d stills" % len(got))
        if not done("proxy"):
            mode = str(cfg.get("proxy") or "auto")
            if mode == "never" or (mode == "auto" and media.browser_playable(r["probe"], src)):
                self.rset(rid, "proxy", "done", note="not needed - the browser plays the recording itself")
            else:
                self.rset(rid, "proxy", "running", label="making the review copy", percent=0)
                media.make_proxy(src, os.path.join(folder, "proxy.mp4"), prog("proxy"))
                self.rset(rid, "proxy", "done", file="proxy.mp4")
        self.note("%s analysed" % rid)

    def media_for_browser(self, rid):
        p = os.path.join(self.rec_dir(rid), "proxy.mp4")
        return p if os.path.exists(p) else self.rec_path(rid)

    def levels(self, rid):
        return analyse.load(self.rec_dir(rid), "levels.json")

    # ---- 3. cuts (gate 1) ----------------------------------------------------------------------------------------
    def cuts(self):
        return segment.load(self.cuts_path)

    def save_cuts(self, data):
        problems = segment.validate(self, segment.normalise(data))
        errors = [p for p in problems if p.startswith("ERROR")]
        if errors:
            raise ValueError("; ".join(errors))
        segment.save(self.cuts_path, segment.annotate(self, data))
        return problems

    def stereo_for(self, cut):
        """The one-sided-stereo check over a cut's parts (the first part with a verdict decides the filter)."""
        out = None
        for p in cut["parts"]:
            lv = self.levels(p["rec"])
            if lv:
                c = stereo.check(lv, p["start"], p["end"], self.cfg)
                if out is None or (c["fix"] and not out["fix"]):
                    out = c
        return out or {"fix": None, "af": None, "note": "no levels yet"}

    def sync_talks(self):
        """state.talks from the approved cuts. A cut changed after it was rendered is done again from scratch."""
        talks = {t["key"]: t for t in events.talks(self.ev)}
        approved = [c for c in self.cuts()["cuts"] if c.get("approved")]
        with self.lock:
            for c in approved:
                sig, t = signature(c["parts"]), self.state["talks"].get(c["key"])
                if t and t.get("cut_sig") != sig and t["steps"]:
                    self.redo(c["key"], "the cut changed after it was made")
                    t = self.state["talks"][c["key"]]
                if not t:
                    info = talks.get(c["key"]) or {}
                    t = self.state["talks"][c["key"]] = {"key": c["key"], "title": info.get("title", ""), "steps": {},
                                                         "current": None, "error": None}
                t["cut_sig"], t["parts"] = sig, c["parts"]
            self.save()
        return [c["key"] for c in approved]

    def redo(self, key, why):
        """Start a talk again from scratch (a changed cut). Its old Descript composition is never reused; an old
        finished file moves to <event>/out/replaced/ (kept), so a stale video can't go to YouTube by mistake."""
        t = self.state["talks"][key]
        old_final = self.final_path(key)
        if os.path.exists(old_final):
            os.makedirs(os.path.join(self.out, "replaced"), exist_ok=True)
            shutil.move(old_final, os.path.join(self.out, "replaced", "%s %s" % (now().replace(":", "-"), os.path.basename(old_final))))
        for leftover in (os.path.join(self.cut_dir, key + ".mp4"), os.path.join(self.out, "_descript", key + ".mp4")):
            if os.path.exists(leftover):
                os.remove(media.safe_target(leftover))
        if t.get("composition_id"):
            t.setdefault("broken_compositions", []).append(t["composition_id"])
        for k in ("composition_id", "composition_name", "finished", "trim", "error", "current", "stereo", "resolution",
                  "zoom", "in_clip"):
            t.pop(k, None)
        if key in self.reviews():
            self.set_reviewed(key, False)               # a new cut is a new video: reviewed again
        t["steps"] = {}
        t.setdefault("redone", []).append({"at": now(), "why": why})
        self.note("%s: %s - doing it again" % (key, why))

    def padded(self, key):
        """The talk's parts with pad_before_seconds before the first and pad_after_seconds after the last, never into
        another talk's own cut on the same recording, nor past the recording. Returns (parts, before, after)."""
        t = self.state["talks"][key]
        parts = [dict(p) for p in t["parts"]]
        others = [p for c in self.cuts()["cuts"] if c["key"] != key for p in c["parts"]]
        first, last = parts[0], parts[-1]
        want_b, want_a = float(self.cfg.get("pad_before_seconds", 30)), float(self.cfg.get("pad_after_seconds", 60))
        floor = max([p["end"] for p in others if p["rec"] == first["rec"] and p["end"] <= first["start"]] + [0.0])
        dur = ((self.state["recordings"].get(last["rec"]) or {}).get("probe") or {}).get("duration") or last["end"]
        ceil = min([p["start"] for p in others if p["rec"] == last["rec"] and p["start"] >= last["end"]] + [dur])
        new_start, new_end = max(floor, first["start"] - want_b), min(ceil, last["end"] + want_a)
        before, after = first["start"] - new_start, new_end - last["end"]
        first["start"], last["end"] = new_start, new_end
        return parts, before, after

    # ---- redo: Marek flags a talk in the workbench (redo.json, written by the app only) -----------------------------
    def redo_requests(self):
        try:
            with open(os.path.join(self.dir, "redo.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def request_redo(self, key, why, on=True):
        r = self.redo_requests()
        if on:
            r[key] = {"at": now(), "why": why or "flagged in the workbench"}
        else:
            r.pop(key, None)
        self.ensure_dirs()
        path = os.path.join(self.dir, "redo.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(r, f, indent=1, ensure_ascii=False)
        os.replace(path + ".tmp", path)

    def apply_redo_requests(self):
        """At the start of a run: every talk flagged since its last redo starts again from the cut - with the cut,
        extra footage and zoom as they are now - and goes to Descript as a fresh composition "<talk> (redo N)". The old
        composition stays in Descript, untouched."""
        done = []
        for key, req in self.redo_requests().items():
            t = self.state["talks"].get(key)
            if not t or t.get("redo_handled") == req["at"]:
                continue
            self.redo(key, "flagged for a redo: " + req["why"])
            t["redo_handled"] = req["at"]
            done.append(key)
        if done:
            self.save()
        return done

    def reviews(self):
        try:
            with open(self.reviews_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def set_reviewed(self, key, on=True):
        r = self.reviews()
        if on:
            r[key] = now()
        else:
            r.pop(key, None)
        self.ensure_dirs()
        tmp = self.reviews_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.reviews_path)

    def final_path(self, key):
        safe = re.sub(r'[\\/:*?"<>|]+', "", key)
        return os.path.join(self.final_dir, "%s - %s.mp4" % (safe, self.ev["title"]))

    # ---- 4. one talk: cut -> Descript -> finished ------------------------------------------------------------------
    def process(self, key):
        t, cfg = self.state["talks"][key], self.cfg
        done = lambda s: (t["steps"].get(s) or {}).get("status") == "done"
        lean = os.path.join(self.cut_dir, key + ".mp4")
        raw = os.path.join(self.out, "_descript", key + ".mp4")
        final = self.final_path(key)
        srt_path = os.path.join(self.srt_dir, re.sub(r"[^\w.-]+", "_", "%s_%s.srt" % (self.ev["id"], key)))
        progress = lambda step: (lambda label, percent=None: self.set(key, step, "running", label=label, percent=percent))
        try:
            with self.lock:
                t["error"] = None
            if not done("cut"):
                with self.render_sem:
                    st = self.stereo_for({"parts": t["parts"]})
                    padded, before, after = self.padded(key)
                    crop, zoom_note = None, None
                    if cfg.get("zoom_black_bars", True):
                        self.set(key, "cut", "running", label="looking for black bars around the slides", percent=0)
                        p0 = t["parts"][0]
                        crop, zoom_note = media.black_bars(self.rec_path(p0["rec"]), p0["start"], p0["end"],
                                                           min_zoom_percent=float(cfg.get("zoom_min_percent", 5)))
                    self.set(key, "cut", "running", label="cutting", percent=0)
                    parts = [(self.rec_path(p["rec"]), p["start"], p["end"]) for p in padded]
                    media.cut(parts, lean, st.get("af"), on_progress=progress("cut"), cq=int(cfg.get("cut_crf") or 18),
                              crop=crop)
                    info = media.probe(lean)
                    core = sum(p["end"] - p["start"] for p in t["parts"])
                    with self.lock:
                        t["stereo"], t["probe"], t["zoom"] = st, info, {"crop": crop, "note": zoom_note}
                        # where the judged talk sits in the clip, for trimming the extra footage by hand in Descript
                        t["in_clip"] = {"start": round(before, 1), "end": round(before + core, 1), "before": round(before, 1),
                                        "after": round(after, 1)}
                        t["resolution"] = media.pick_resolution(info["width"], info["height"], cfg["max_resolution"])
                    self.set(key, "cut", "done", sig=t["cut_sig"], note="%s; the talk runs %s-%s in the clip; %s%s" % (
                        segment.stamp(info["duration"]), segment.stamp(before), segment.stamp(before + core), st["note"],
                        "; " + zoom_note if crop else ""))
            d = Descript()
            pname = cfg["project_name"].format(event=self.ev["title"])
            redos = len(t.get("redone") or [])
            comp_name = key if not redos else "%s (redo %d)" % (key, redos + 1)   # a redo never hides the old version
            # credit guard 1: a composition with this name already in the Descript project -> reuse it
            if not done("upload"):
                with self.plock:
                    if not self.state["project_id"]:
                        self.state["project_id"] = d.find_project(pname)
                if self.state["project_id"]:
                    bad = set(t.get("broken_compositions") or [])
                    existing = {c["name"]: c for c in d.project(self.state["project_id"]).get("compositions", [])
                                if c["id"] not in bad and (c.get("duration") or 0) >= MIN_TALK_SECONDS}
                    if comp_name in existing:
                        t["composition_id"], t["composition_name"] = existing[comp_name]["id"], comp_name
                        self.set(key, "upload", "done", note="already in Descript, reused")
            if not done("upload"):
                self.set(key, "upload", "running", label="uploading %.0f MB" % (os.path.getsize(lean) / 1e6), percent=0)
                with self.plock:
                    created = False
                    if not self.state["project_id"]:
                        pid, cid, _ = d.import_file(lean, comp_name, project_name=pname, on_progress=progress("upload"))
                        self.state["project_id"], created = pid, True
                        self.save()
                if not created:
                    again = len(t.get("broken_compositions") or [])
                    pid, cid, _ = d.import_file(lean, comp_name, project_id=self.state["project_id"], on_progress=progress("upload"),
                                                media_name=comp_name if not again else "%s (upload %d)" % (comp_name, again + 1))
                comp = next((c for c in d.project(self.state["project_id"]).get("compositions", []) if c["id"] == cid), {})
                if (comp.get("duration") or 0) < MIN_TALK_SECONDS:
                    t.setdefault("broken_compositions", []).append(cid)
                    raise RuntimeError("Descript got no usable video from the upload (%.0f s)" % (comp.get("duration") or 0))
                t["composition_id"], t["composition_name"] = cid, comp_name
                self.set(key, "upload", "done", note=comp_name if comp_name != key else None)
            pid, cid = self.state["project_id"], t["composition_id"]
            # credit guard 2: never pay for the same edit twice (the ledger, or a composition already shorter)
            if not done("edit"):
                paid = self.ledger().get(cid)
                if not paid:
                    pr = d.project(pid)
                    comp = next((c for c in pr.get("compositions", []) if c["id"] == cid), {})
                    media_len = ((pr.get("media_files") or {}).get(key) or {}).get("duration") or 0
                    if comp and media_len and comp.get("duration", media_len) < media_len * 0.99:
                        paid = {"note": "composition already shorter than its media"}
                if paid:
                    self.set(key, "edit", "done", note="already edited in Descript, skipped (%s)" % (paid.get("note") or paid.get("at")))
            if not done("edit"):
                if self.state.get("edits_paused"):
                    with self.lock:
                        t["current"] = "edit"                # the error belongs to the edit, not the finished upload
                    raise RuntimeError("edits paused: " + self.state["edits_paused"])
                self.set(key, "edit", "running")
                j = d.edit(pid, cid, cfg["edit_prompt"].format(composition=comp_name, **cfg), on_progress=progress("edit"))
                credits = float(j["result"].get("ai_credits_used") or 0)
                self.record_edit(cid, key, credits)
                report = str(j["result"].get("agent_response") or "")
                self.set(key, "edit", "done", report=report, credits=credits, job_id=j.get("job_id"))
                # another talk named in the report - unless the sentence says it was left alone ("I didn't open or
                # edit 'Anderson Gobbi'", which is how the AI editor confirms it stayed in its lane)
                sentences = re.split(r"(?<=[.!?])\s+|\n", report)
                others = [k for k in self.state["talks"] if k != key and any(
                    k in sn and not re.search(r"\b(didn'?t|did not|not|never|no other|only|without|untouched|left alone)\b",
                                              sn, re.I) for sn in sentences)]
                cap = float(cfg.get("max_edit_credits") or 0)
                if re.search(r"\ball \d+ (clips|compositions)\b", report, re.I) or others:
                    with self.lock:
                        self.state["edits_paused"] = ("%s's edit report mentions %s - the AI editor may have edited other "
                                                      "talks; check it in Descript, then clear edits_paused in state.json"
                                                      % (key, ", ".join(others) or "all clips"))
                    self.note("EDITS PAUSED: " + self.state["edits_paused"])
                elif cap and credits > cap:
                    with self.lock:
                        self.state["edits_paused"] = ("%s's edit cost %.1f AI credits (limit %g) - check it in Descript, "
                                                      "then clear edits_paused in state.json" % (key, credits, cap))
                    self.note("EDITS PAUSED: " + self.state["edits_paused"])
            # GATE 2: Marek looks at the edited talk in Descript before anything is exported
            reviewed = self.reviews().get(key)
            if not reviewed:
                if (t["steps"].get("review") or {}).get("status") != "waiting":
                    self.set(key, "review", "waiting", label="check it in Descript, then tick Reviewed")
                    self.note("%s is ready for review in Descript" % key)
                    self.publish()
                with self.lock:
                    t["current"] = "review"
                return
            if not done("review"):
                self.set(key, "review", "done", note="reviewed %s" % reviewed)
            if not done("publish"):
                self.set(key, "publish", "running")
                j = d.publish(pid, cid, t.get("resolution") or "1080p", cfg["publish_access"], on_progress=progress("publish"))
                self.set(key, "publish", "done", job_id=j.get("job_id"), share_url=j["result"].get("share_url"),
                         download_url=j["result"].get("download_url"))
            if not done("download"):
                self.set(key, "download", "running")
                os.makedirs(os.path.dirname(raw), exist_ok=True)
                with requests.get(t["steps"]["publish"]["download_url"], stream=True, timeout=600) as r:
                    r.raise_for_status()
                    with open(raw + ".part", "wb") as f:
                        for chunk in r.iter_content(16 << 20):
                            f.write(chunk)
                os.replace(raw + ".part", raw)
                self.set(key, "download", "done")
            if not done("srt"):
                self.set(key, "srt", "running")
                with open(srt_path, "wb") as f:
                    f.write(d.srt(pid, cid))
                self.set(key, "srt", "done", file=os.path.basename(srt_path))
            if not done("chapters"):
                self.chapters_step(key, d)
            if not done("loudness"):
                self.set(key, "loudness", "running", label="ffmpeg to %s LUFS" % cfg["loudness_lufs"])
                trim = self.plan_trim(key, raw, srt_path)
                how = media.normalize(raw, final, cfg["loudness_lufs"], cfg["true_peak_db"], trim.get("start"),
                                      trim.get("end"), crf=cfg.get("final_crf"), preset=cfg.get("final_preset") or "medium",
                                      audio_kbps=int(cfg.get("final_audio_kbps") or 160))
                self.apply_trim(key, srt_path)
                self.set(key, "loudness", "running", label="encoded (%s)" % how)
                self.set(key, "loudness", "done", file=os.path.basename(final), how=how,
                         mb=round(os.path.getsize(final) / 1e6), export_mb=round(os.path.getsize(raw) / 1e6),
                         note="%s, %.0f MB (Descript's export %.0f MB)%s" % (
                             how, os.path.getsize(final) / 1e6, os.path.getsize(raw) / 1e6,
                             "; trimmed %.1f s at the start, %.1f s at the end" % (trim.get("start") or 0, trim.get("tail") or 0)
                             if trim.get("start") or trim.get("tail") else ""))
            if not done("qa"):
                self.set(key, "qa", "running")
                lufs, peak = media.loudness(final)
                out = media.probe(final)
                before = (t.get("probe") or {}).get("duration") or 0
                cut = 100 * (1 - out["duration"] / before) if before else 0
                flags = []
                if lufs is None or abs(lufs - cfg["loudness_lufs"]) > cfg["qa_loudness_tolerance"]:
                    flags.append("loudness %s LUFS" % lufs)
                if cut > cfg["qa_max_cut_percent"]:
                    flags.append("edit removed %.0f%% of the talk" % cut)
                if out["height"] and min(out["width"], out["height"]) < 480:
                    flags.append("low resolution")
                self.set(key, "qa", "done", lufs=lufs, peak=peak, minutes_before=round(before / 60, 1),
                         minutes_after=round(out["duration"] / 60, 1), cut_percent=round(cut, 1), flags=flags)
            with self.lock:
                t["current"] = None
                t["finished"] = now()
                self.save()
            self.note("done: %s" % key)
            self.publish()
        except Exception as ex:
            with self.lock:
                t["error"] = "%s: %s" % (t.get("current") or "?", ex)
                cur = t.get("current")
                if cur and (t["steps"].get(cur) or {}).get("status") != "done":   # never overwrite a finished step
                    t["steps"][cur] = {"status": "failed", "at": now(), "error": str(ex)[:500]}
                self.save()
            self.note("FAILED %s at %s: %s" % (key, t.get("current"), ex))
            traceback.print_exc()

    def chapters_step(self, key, d=None):
        """YouTube chapters (and a short title when the real one is too long) from Descript's AI editor, read-only."""
        t, d = self.state["talks"][key], d or Descript()
        talk = next((x for x in events.talks(self.ev) if x["key"] == key), None)
        self.set(key, "chapters", "running", label="Descript is writing chapters")
        who = youtube.speakers_of(talk) if talk else key
        room = youtube.budget(self.ev, self.cfg, who)
        text, res = d.chapters(self.state["project_id"], t["composition_id"], t.get("composition_name") or key,
                               on_progress=lambda label, percent=None: self.set(key, "chapters", "running",
                                                                                label=label, percent=percent),
                               title=talk["title"] if talk and len(talk["title"]) > room else None, title_room=room)
        m = re.search(r"^\s*TITLE:\s*(.+?)\s*$", text, re.M | re.I)
        if m and not t.get("yt_title"):
            with self.lock:
                t["yt_title"] = m.group(1).strip().strip('"')
        chapters, problem = youtube_chapters(text)
        self.set(key, "chapters", "done", chapters=chapters, credits=res.get("ai_credits_used"),
                 note=problem or "%d chapters" % len(chapters), raw=None if chapters else text[:500])
        if (t.get("trim") or {}).get("applied"):
            self.shift_chapters(key)
        self.write_youtube()

    # ---- trim to the speech (as the Conf42 factory) ---------------------------------------------------------------
    def plan_trim(self, key, raw, srt_path):
        t, cfg = self.state["talks"][key], self.cfg
        if "trim" in t:
            return t["trim"]
        trim = {}
        cues = srt_cues(srt_path) if cfg.get("trim_to_speech", True) and os.path.exists(srt_path) else []
        if cues:
            lead, tail = float(cfg.get("trim_lead_seconds", 0.3)), float(cfg.get("trim_tail_seconds", 1.0))
            start = cues[0][0] - lead
            if 0.5 <= start <= 300:
                trim["start"] = round(start, 3)
            length = media.probe(raw)["duration"]
            end = cues[-1][1] + tail
            if length and length - end > 1.5:
                trim["end"], trim["tail"] = round(end, 3), round(length - end, 1)
        with self.lock:
            t["trim"] = trim
            self.save()
        return trim

    def apply_trim(self, key, srt_path):
        t = self.state["talks"][key]
        trim = t.get("trim") or {}
        if trim.get("applied"):
            return
        if trim.get("start") and os.path.exists(srt_path):
            shift_srt(srt_path, trim["start"])
        with self.lock:
            trim["applied"] = True
            self.save()
        self.shift_chapters(key)

    def shift_chapters(self, key):
        t = self.state["talks"][key]
        step, off = t["steps"].get("chapters") or {}, (t.get("trim") or {}).get("start") or 0
        if not off or not step.get("chapters") or step.get("trimmed"):
            return
        out = []
        for i, c in enumerate(step["chapters"]):
            sec = 0 if i == 0 else max(0, _secs(c["at"]) - int(round(off)))
            if out and sec - out[-1][0] < 10:
                continue
            out.append((sec, c["title"]))
        with self.lock:
            step["chapters"], step["trimmed"] = [{"at": _stamp(s), "title": x} for s, x in out], off
            self.save()

    def write_youtube(self):
        """<event>/YouTube items.txt: per talk, in schedule (sheet) order, the YouTube title, the description, and
        the link to its thumbnail - ready to paste."""
        lines = ["%s - YouTube items" % self.ev["title"],
                 "Per talk: TITLE goes in the YouTube title, everything under DESCRIPTION into the description, and "
                 "THUMBNAIL is the picture to upload (drawn by the website).", ""]
        missing = []
        for talk in events.talks(self.ev):
            t = self.state["talks"].get(talk["key"])
            ch = ((t or {}).get("steps", {}).get("chapters") or {}).get("chapters") if t else None
            if not t or not t.get("finished") and not ch:
                missing.append(talk["key"])
                continue
            title, desc = youtube.block(self.ev, self.cfg, talk, ch, t.get("yt_title"))
            lines += ["=" * 78, "%s - %s" % (talk["key"], talk["title"]), "=" * 78,
                      "FILE: %s" % os.path.basename(self.final_path(talk["key"])),
                      "THUMBNAIL: %s" % youtube.thumbnail_url(self.ev, talk), "",
                      "TITLE (%d of %d characters)" % (len(title), int((self.cfg.get("youtube") or {}).get("title_max") or 100)),
                      title, "", "DESCRIPTION"] + desc + ["", ""]
        if missing:
            lines.append("(not ready yet: %s)" % ", ".join(missing))
        with open(os.path.join(self.dir, "YouTube items.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def process_with_retries(self, key, attempts=3, pause=120):
        t = self.state["talks"][key]
        for n in range(1, attempts + 1):
            self.process(key)
            err = t.get("error") or ""
            if not err or n == attempts or "402" in err or "edits paused" in err:
                return
            with self.lock:
                t["retry"] = "failed (%s) - trying again in %d min, attempt %d of %d" % (err[:120], pause // 60, n + 1, attempts)
                self.save()
            self.note("%s failed, retrying in %d min (attempt %d of %d)" % (key, pause // 60, n + 1, attempts))
            time.sleep(pause)
        with self.lock:
            t["retry"] = None

    def run(self, only=None):
        """Every approved cut through cut -> Descript -> (review) -> finished. Talks waiting for review stop there;
        run again after ticking Reviewed and they carry on."""
        bad = [p for p in segment.validate(self, self.cuts()) if p.startswith("ERROR")]
        if bad:
            raise SystemExit("cuts.json has errors - fix them first: " + "; ".join(bad))
        self.sync_talks()
        redone = self.apply_redo_requests()
        keys = [k for k in self.sync_talks() if not self.state["talks"][k].get("finished") and (not only or k in only)]
        if redone:
            self.note("redo: %s - cut again and uploaded as a fresh composition" % ", ".join(redone))
        if keys:
            self.ensure_space("cutting and exporting %d talk(s)" % len(keys))
        if not keys:
            self.note("nothing to do: no approved cuts waiting (approve them in the dashboard first)")
            return
        if any(not ((self.state["recordings"].get(p["rec"]) or {}).get("steps", {}).get("copy") or {}).get("status") == "done"
               and not self.state["recordings"][p["rec"]].get("in_place")
               for k in keys for p in self.state["talks"][k]["parts"]):
            raise SystemExit("some recordings are not copied in yet - run analyse first")
        with self.lock:
            for k in keys:
                t = self.state["talks"][k]
                for step in list(t["steps"]):
                    if (t["steps"][step] or {}).get("status") in ("running", "failed"):
                        del t["steps"][step]
                t.update(current=None, error=None, retry=None)
            self.save()
        keys.sort(key=lambda k: k.casefold())        # the Descript project lists the talks A-Z
        self.note("processing %d talk(s), %d at a time" % (len(keys), int(self.cfg.get("parallel_talks") or 2)))
        if self.state.get("project_id"):
            try:
                gone = Descript().cancel_orphan_uploads(self.state["project_id"])
                if gone:
                    self.note("cancelled %d Descript upload(s) left over from a stopped run" % len(gone))
            except Exception as ex:
                self.note("could not check for leftover Descript uploads: %s" % ex)
        with ThreadPoolExecutor(max_workers=max(1, int(self.cfg.get("parallel_talks") or 2))) as pool:
            list(pool.map(self.process_with_retries, keys))
        self.write_youtube()
        waiting = [k for k in keys if (self.state["talks"][k]["steps"].get("review") or {}).get("status") == "waiting"]
        self.note("run finished" + (" - %d talk(s) waiting for your review in Descript" % len(waiting) if waiting else ""))


# ---- SRT and chapters (the Conf42 factory's) --------------------------------------------------------------------------
def _srt_time(s):
    h, m, rest = s.strip().split(":")
    sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000.0


def _srt_stamp(x):
    ms = int(round(x * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def srt_cues(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return [(_srt_time(a), _srt_time(b)) for a, b in re.findall(r"(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)", f.read())]


def shift_srt(path, by):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        blocks = re.split(r"\n\s*\n", f.read().replace("\r\n", "\n").strip())
    out = []
    for b in blocks:
        lines = b.split("\n")
        i = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if i is None:
            continue
        a, z = [_srt_time(x) - by for x in lines[i].split("-->")]
        if z <= 0:
            continue
        out.append("%d\n%s --> %s\n%s" % (len(out) + 1, _srt_stamp(max(0, a)), _srt_stamp(z), "\n".join(lines[i + 1:])))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(out) + "\n")


def _secs(stamp):
    parts = [int(x) for x in stamp.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def _stamp(sec):
    """Chapter times as the channels write them: 00:00, 12:46, 1:02:03."""
    return "%d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60) if sec >= 3600 else "%02d:%02d" % (sec // 60, sec % 60)


def youtube_chapters(text, length=None):
    """Chapter lines -> [{"at", "title"}] that YouTube accepts: first at 0:00, ascending, >= 10 s apart, at least 3."""
    found = []
    for line in (text or "").splitlines():
        m = re.match(r"^[-*\s]*\(?(\d{1,2}(?::\d{2}){1,2})\)?[\s:\-]*(.+)$", line.strip())
        if m and m.group(2).strip():
            found.append((_secs(m.group(1)), m.group(2).strip().strip("-").strip()))
    found.sort()
    out = []
    for sec, title in found:
        if length and sec >= length - 5:
            continue
        if out and sec - out[-1][0] < 10:
            continue
        out.append((sec, title))
    if out and out[0][0] != 0:
        out[0] = (0, out[0][1])
    if len(out) < 3:
        return [], "Descript gave %d usable chapter(s) - YouTube needs 3+" % len(out)
    return [{"at": _stamp(s), "title": t} for s, t in out], None


def summary(state):
    talks, recs = state.get("talks", {}), state.get("recordings", {})
    return {"recordings": len(recs),
            "analysed": [k for k, r in recs.items() if all((r["steps"].get(s) or {}).get("status") == "done"
                                                          for s in REC_STEPS if s != "copy" or not r.get("in_place"))],
            "talks": len(talks), "finished": [k for k, t in talks.items() if t.get("finished")],
            "failed": [k for k, t in talks.items() if t.get("error")],
            "review": [k for k, t in talks.items() if (t["steps"].get("review") or {}).get("status") == "waiting"],
            "flagged": {k: t["steps"]["qa"]["flags"] for k, t in talks.items() if (t["steps"].get("qa") or {}).get("flags")}}
