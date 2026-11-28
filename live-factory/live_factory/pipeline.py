"""The LIVE talk factory pipeline: long live recordings -> one clean video per talk, Descript only for the talks.

  add       copy the recordings in (from an SD card or a disk), each tied to a day + track of the schedule
  analyse   per recording, locally and for free: channel levels, Whisper transcript, frames, a browser proxy
  segment   Claude matches the transcript to the lineup -> cuts.json (segment.py; `draft` makes a first guess)
  GATE 1    Marek checks and approves the cuts in the dashboard - nothing reaches Descript before that
  per talk: cut (ffmpeg, one-sided stereo filled) -> upload -> edit (Studio Sound, fillers, gaps)
  GATE 2    Marek reviews each talk in Descript and ticks it Reviewed
  per talk: publish -> download -> srt -> chapters -> loudness (+ trim to the speech) -> qa -> selfcheck
  then      YouTube items.txt: titles, descriptions, chapters, thumbnail links
  AUTOPILOT ("autopilot": true in cuts.json, Marek 2026-11-27: "ask for forgiveness not for permission"): Claude
            approves the cuts itself, gate 2 is the factory's own check of the final (selfcheck.py), and the checked
            talks go to the brand's YouTube channel - private, public 5 weeks after the event (ytupload.py) - and
            into "ship report.md".

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
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests
import yaml

from . import (analyse, audiosync, ends, events, machine, media, picture, precheck, proofread, saturation, segment,
               selfcheck, stereo, youtube, ytupload)
from .descript import Descript

HERE = events.HERE
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".mts", ".m2ts", ".avi", ".webm", ".mxf")
MEDIA_EXT = VIDEO_EXT + audiosync.AUDIO_EXT     # a sound-only file is a separate sound recording (audiosync.py)
PICTURE_VERSION = 2                             # bump when picture.py's plans change, so cached plans are made again
REC_STEPS = ["copy", "levels", "transcript", "frames", "proxy"]
STEPS = ["cut", "check", "upload", "edit", "review", "publish", "download", "srt", "proofread", "chapters", "loudness",
         "qa", "selfcheck"]
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


WAIT = time.sleep                                        # tests swap it out


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
        self.ends_lock = threading.Lock()                    # one close listening at a time (the GPU)
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
            machine.swap_in(tmp, self.state_path)

    @contextlib.contextmanager
    def job(self, name):
        """A background job owns state.json while it runs: its name and a heartbeat every 30 s tell the dashboard
        (which then starts no second job on this event)."""
        lock = os.path.join(self.dir, "job.lock")
        self.ensure_dirs()
        for _ in range(2):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, ("%d %s" % (os.getpid(), name)).encode())
                os.close(fd)
                break
            except FileExistsError:
                if time.time() - os.path.getmtime(lock) > 180:   # its heartbeat stopped: a crashed job's
                    os.remove(lock)
                    continue
                with open(lock, encoding="utf-8", errors="replace") as f:
                    who = f.read().strip()
                raise SystemExit("another job is running on this event (%s) - wait for it, or stop it first" % who)
        with self.lock:
            self.state["job"], self.state["run_started"] = name, now()
            self.save()
        stop = threading.Event()
        def beat():
            while not stop.wait(30):
                try:
                    self.save()
                    os.utime(lock, None)
                except Exception as ex:                  # noqa: BLE001 - the heartbeat never dies
                    print("heartbeat: %s" % ex, flush=True)
        threading.Thread(target=beat, daemon=True).start()
        awake = machine.keep_awake().__enter__()         # no sleep while a job runs (hours, unattended)
        try:
            yield
        finally:
            awake.__exit__()
            stop.set()
            try:
                os.remove(lock)
            except OSError:
                pass
            with self.lock:
                self.state["runner"], self.state["job"] = {"pid": None, "at": now(), "finished": True}, None
                tmp = self.state_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, indent=1, ensure_ascii=False)
                machine.swap_in(tmp, self.state_path)
            self.publish(force=True)

    def busy(self):
        """The job running on this event right now (its job.lock, kept fresh by the heartbeat), or None."""
        lock = os.path.join(self.dir, "job.lock")
        try:
            if time.time() - os.path.getmtime(lock) > 180:
                return None
            with open(lock, encoding="utf-8", errors="replace") as f:
                return f.read().strip() or "a job"
        except OSError:
            return None

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
        """The paid Descript edits of every event on this computer. A missing ledger is empty; a damaged one stops the
        edits (read as empty it would let an edit be paid for again)."""
        try:
            with open(self.ledger_path(), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as ex:
            raise RuntimeError("the ledger of paid Descript edits (%s) can't be read (%s) - fix or restore it before "
                               "any edit" % (self.ledger_path(), ex))

    def _ledger_write(self, composition_id, entry):
        """One entry into the ledger: under a lock file (several events can run on one computer), written whole and
        swapped in."""
        lock = self.ledger_path() + ".lock"
        for _ in range(600):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                if time.time() - os.path.getmtime(lock) > 120:
                    os.remove(lock)                      # a crashed writer's
                    continue
                time.sleep(0.1)
        try:
            with self.lock:
                led = self.ledger()
                led[composition_id] = dict(led.get(composition_id) or {}, **entry)
                tmp = self.ledger_path() + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(led, f, indent=1, ensure_ascii=False)
                machine.swap_in(tmp, self.ledger_path())
        finally:
            try:
                os.remove(lock)
            except OSError:
                pass

    def record_edit_start(self, composition_id, key, job_id):
        """Written BEFORE waiting on the edit: a crash or a network blip after this resumes the same job, never pays
        for a second one."""
        self._ledger_write(composition_id, {"event": self.ev["id"], "talk": key, "job_id": job_id, "started": now(),
                                            "done": False})

    def record_edit(self, composition_id, key, credits):
        self._ledger_write(composition_id, {"event": self.ev["id"], "talk": key, "credits": credits, "at": now(),
                                            "done": True})

    # ---- 1. add: recordings in, each tied to a day + track ------------------------------------------------------
    def add(self, paths, day=None, track=None, in_place=False, sound=False):
        """Register recordings (files, or folders of them). Copied into <event>/in/ unless in_place (then read where
        they are - an external SSD that stays plugged in). The source is never changed or deleted. Sound-only files
        (.wav, .mp3, ...) - and any file with sound=True - are separate sound recordings: never cut from, their
        sound replaces the camera's where they cover a talk (audiosync.py). Returns the ids."""
        files = []
        for p in paths:
            p = os.path.abspath(os.path.expanduser(p))
            if os.path.isdir(p):
                files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(audiosync.AUDIO_EXT if sound else VIDEO_EXT)
                                and not f.startswith("."))
            elif p.lower().endswith(MEDIA_EXT):
                files.append(p)
            else:
                raise ValueError("%s is not a video or a sound file (%s)" % (p, ", ".join(MEDIA_EXT)))
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
                if sound or src.lower().endswith(audiosync.AUDIO_EXT):
                    self.state["recordings"][rid]["kind"] = "sound"
                self.save()
            added.append(rid)
            self.note("%s: %s - day %d, track %s (%s)%s%s" % (
                rid, os.path.basename(src), sch["day"], tr, sch["room"],
                " - separate SOUND: placed on the videos after the analysis" if self.is_sound(rid) else "",
                " - read in place: that file is the ONLY copy, back it up" if in_place else ""))
        return added

    def is_sound(self, rid):
        return (self.state["recordings"].get(rid) or {}).get("kind") == "sound"

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

    def talk_bytes(self, key, what):
        """Bytes one talk's next local file takes: "cut" = the padded cut, "export" = Descript's export + the lean final."""
        mb = 1e6
        secs = sum(p["end"] - p["start"] for p in self.padded(key)[0])
        if what == "export":
            return secs * 1.4 * mb
        recs = [self.state["recordings"].get(p["rec"]) or {} for p in self.state["talks"][key]["parts"]]
        rates = [min(r["size"] / r["probe"]["duration"], 1.25 * mb) for r in recs
                 if r.get("size") and (r.get("probe") or {}).get("duration")]
        return secs * (max(rates) if rates else 1.25 * mb)

    def ensure_room(self, key, what):
        """Checked again right before each talk writes a big file, not only when the run starts: two talks work at
        once and the other Mac users keep writing, so the start-of-run sum can go stale (LLMday Austin: 67 GB free at
        the start, 11 GB by Geoff's upload)."""
        where = self.dir
        while not os.path.isdir(where):
            where = os.path.dirname(where)
        free = shutil.disk_usage(where).free
        reserve = float(self.cfg.get("disk_reserve_gb", 10)) * 1e9
        need = self.talk_bytes(key, what)
        if need + reserve > free:
            self.space_alarm("%s's %s needs about %.1f GB, %.0f GB is free (keeping %.0f GB spare) - free %.0f GB (ask "
                             "Claude to clean up junk and cache files), then run the factory again" % (
                                 key, "cut" if what == "cut" else "Descript export", need / 1e9, free / 1e9,
                                 reserve / 1e9, (need + reserve - free) / 1e9))

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
        self.sync_sound()

    # ---- separate sound: where each sound recording sits on the videos (audiosync.py) ---------------------------
    def sync_sound(self, force=False):
        """Place every separate sound recording on the videos of its day. Done again when videos were added since."""
        if not self.cfg.get("separate_sound", True):
            return
        recs = self.state["recordings"]
        for sid in sorted((r for r in recs if self.is_sound(r)), key=lambda k: int(k[1:])):
            s = recs[sid]
            if (s["steps"].get("levels") or {}).get("status") != "done":
                continue
            vids = sorted(rid for rid, r in recs.items() if not self.is_sound(rid) and r.get("day") == s.get("day")
                          and (r["steps"].get("levels") or {}).get("status") == "done")
            step = s["steps"].get("sync") or {}
            if not force and step.get("status") == "done" and step.get("videos") == vids:
                continue
            if not vids:
                self.rset(sid, "sync", "done", videos=[], note="no video of day %s to place it on yet" % s.get("day"))
                continue
            try:
                self.rset(sid, "sync", "running", label="placing the sound on the videos", percent=0)
                data = audiosync.sync(sid, self.rec_path(sid), os.path.join(self.rec_dir(sid), "fingerprint.npy"),
                                      [(v, self.rec_path(v), os.path.join(self.rec_dir(v), "fingerprint.npy")) for v in vids],
                                      lambda label, pct=None: self.rset(sid, "sync", "running", label=label, percent=pct))
                audiosync.save(os.path.join(self.rec_dir(sid), "sync.json"), data)
                self.rset(sid, "sync", "done", videos=vids, stretches=len(data["stretches"]),
                          note="; ".join(data["notes"][:3]))
                for n in data["notes"]:
                    self.note(n)
            except Exception as ex:
                self.rset(sid, "sync", "failed", error=str(ex)[:400])
                self.note("FAILED placing %s on the videos: %s" % (sid, ex))
                traceback.print_exc()

    def syncs(self):
        """{sound rid: sync.json} of every placed sound recording."""
        out = {}
        for rid in self.state["recordings"]:
            if self.is_sound(rid):
                d = audiosync.load(os.path.join(self.rec_dir(rid), "sync.json"))
                if d:
                    out[rid] = d
        return out

    def sound_for(self, cut, parts):
        """The separate sound for each (padded) part: ([compose() sound pieces or None per part], notes)."""
        if cut.get("sound") == "camera":
            return [None] * len(parts), ["sound: the camera's own (separate sound off for this talk)"]
        syncs = self.syncs() if self.cfg.get("separate_sound", True) else {}
        if not syncs:
            return [None] * len(parts), []
        out, notes = [], []
        for p in parts:
            vlv = self.levels(p["rec"])
            pieces = []
            for sid, st, a, b in audiosync.pieces(syncs, p["rec"], p["start"], p["end"]):
                slv = self.levels(sid)
                use, why = audiosync.choose(vlv, slv, a, b, st)
                notes.append("sound %s-%s of the clip: %s (%s)" % (segment.stamp(a - p["start"]), segment.stamp(b - p["start"]),
                                                                    why, sid))
                if not use:
                    continue
                s_from, s_to = (a - st["shift"]) / st["scale"], (b - st["shift"]) / st["scale"]
                fix = stereo.check(slv, s_from, s_to, self.cfg) if slv else {"af": None}
                sp_s, _ = audiosync.level_db(slv, s_from, s_to)
                sp_v, _ = audiosync.level_db(vlv, a, b)
                gain = max(-20.0, min(20.0, sp_s - sp_v)) if sp_s is not None and sp_v is not None else 0.0
                for k0, k1, why2 in self.unclipped_runs(self.rec_path(sid), s_from, s_to, self.rec_path(p["rec"]), a, b,
                                                        st["scale"], cut):
                    if why2:
                        notes.append("sound %s-%s of the clip: %s" % (
                            segment.stamp(a - p["start"] + k0 * st["scale"]),
                            segment.stamp(a - p["start"] + k1 * st["scale"]), why2))
                        continue
                    pieces.append({"src": self.rec_path(sid), "start": s_from + k0, "end": min(s_to, s_from + k1),
                                   "scale": st["scale"], "at": a - p["start"] + k0 * st["scale"], "af": fix.get("af"),
                                   "own_gain_db": gain})
            if pieces:
                covered = sum((x["end"] - x["start"]) * x["scale"] for x in pieces)
                notes.append("separate sound covers %d%% of the clip from %s" % (
                    100 * covered / max(1.0, p["end"] - p["start"]), p["rec"]))
            out.append(pieces or None)
        return out, notes

    # ---- the picture pass: deck pages over the slides, night mode put right (picture.py) -------------------------
    def picture_for(self, key, cut, parts, on_progress=None, force=False):
        """The picture plan of each (padded) part, cached in cuts/<key>.picture.json while the parts and the deck stay
        the same. Returns ([plan or None per part], deck path, notes)."""
        if cut.get("picture") == "video" or not (self.cfg.get("slides_replace", True) or self.cfg.get("night_mode_fix", True)):
            return [None] * len(parts), None, ["picture: the recording as it is (picture pass off%s)" % (
                " for this talk" if cut.get("picture") == "video" else "")]
        why = picture.available()
        if why:
            return [None] * len(parts), None, [why]
        deck = picture.find_deck(self, key, cut)
        sig = json.dumps([[p["rec"], round(p["start"], 2), round(p["end"], 2)] for p in parts] +
                         [deck, os.path.getmtime(deck) if deck else 0, PICTURE_VERSION, cut.get("deck_match"),
                          bool(self.cfg.get("slides_replace", True)), bool(self.cfg.get("night_mode_fix", True))])
        path = os.path.join(self.cut_dir, self.fname(key) + ".picture.json")
        cached = picture.load(path)
        if cached and cached.get("sig") == sig and not force:
            plans = cached["plans"]
        else:
            plans = []
            for i, p in enumerate(parts):
                prog = (lambda label, pct=None, i=i: on_progress and on_progress(
                    ("part %d: %s" % (i + 1, label)) if len(parts) > 1 else label, pct))
                plans.append(picture.plan(self.rec_path(p["rec"]), p["start"], p["end"], deck, self.cfg, prog,
                                          loose=cut.get("deck_match") == "loose"))
            os.makedirs(self.cut_dir, exist_ok=True)
            picture.save(path, {"sig": sig, "deck": deck, "plans": plans, "at": now()})
            for i, (p, pl) in enumerate(zip(parts, plans)):
                try:
                    if pl:
                        picture.contact_sheet(self.rec_path(p["rec"]), pl, p["start"], p["end"],
                                              os.path.join(self.cut_dir, "%s.picture%s.jpg" % (self.fname(key), "" if i == 0 else "-%d" % (i + 1))),
                                              deck)
                except Exception as ex:
                    self.note("%s: the before/after sheet failed: %s" % (key, ex))
        notes = [n for pl in plans if pl for n in pl["notes"]]
        return plans, deck, notes

    # ---- saturated sound (saturation.py) --------------------------------------------------------------------------
    # Marek, 2026-11-27: "if there's many tracks available, pick the better one and discard the bad one; if there's
    # NO CHOICE, sample the bad audio, run it through the tools, judge the effect, ship for the whole talk if it's
    # good; if no good effect with each, studio sound at 100% in Descript as the last resort. A sound track saved by
    # the open source tools gets our normal studio sound numbers."
    def span_clipping(self, src, start, end):
        """saturation.scan of a stretch of a recording, cached in the work folder (rec/clipping.json)."""
        path = os.path.join(self.dir, "rec", "clipping.json")
        k = "%s|%.1f|%.1f" % (os.path.basename(src), start, end)
        with self.lock:
            cache = saturation.load(path) or {}
        if k not in cache:
            res = saturation.scan(src, start, end)
            res.pop("tens", None)
            with self.lock:
                cache = saturation.load(path) or {}
                cache[k] = res
                os.makedirs(os.path.dirname(path), exist_ok=True)
                saturation.save(path, cache)
        return cache[k]

    def unclipped_runs(self, snd, s_from, s_to, cam, a, b, scale, cut):
        """The separate sound's stretch in minutes: [(from, to (seconds into the stretch), None to use it / why not)].
        A minute where the recorder is clipped and the camera is not goes to the camera."""
        length = s_to - s_from
        if not self.cfg.get("declip", True) or cut.get("declip") == "off" or length < 60:
            return [(0.0, length, None)]
        rs, cs = self.span_clipping(snd, s_from, s_to), self.span_clipping(cam, a, b)
        if not rs["some"]:
            return [(0.0, length, None)]
        bad = [n >= saturation.CLIPPED_PER_MIN and (i >= len(cs["per_min"]) or cs["per_min"][i] < saturation.CLIPPED_PER_MIN)
               for i, n in enumerate(rs["per_min"])]
        runs, i = [], 0
        while i < len(bad):
            j = i
            while j < len(bad) and bad[j] == bad[i]:
                j += 1
            runs.append((60.0 * i, min(length, 60.0 * j),
                         "the recorder is clipped here and the camera is not: the camera's sound" if bad[i] else None))
            i = j
        return runs
    def clipping_for(self, key, cut, parts, force=False):
        """Clipping in the sound this talk will use (the separate sound where it is laid in, else the camera's), before
        anything is cut. Cached in cuts/<key>.clipping.json while the parts stay the same."""
        sounds, _ = self.sound_for(cut, parts)
        spans = []
        for p, snd in zip(parts, sounds):
            if snd:
                spans += [(x["src"], x["start"], x["end"]) for x in snd]
            else:
                spans.append((self.rec_path(p["rec"]), p["start"], p["end"]))
        sig = json.dumps([[os.path.basename(s), round(a, 1), round(b, 1)] for s, a, b in spans])
        path = os.path.join(self.cut_dir, self.fname(key) + ".clipping.json")
        cached = saturation.load(path)
        if cached and cached.get("sig") == sig and not force:
            return cached["scan"]
        per_min = []
        for s, a, b in spans:
            per_min += saturation.scan(s, a, b)["per_min"]
        res = saturation.summary(per_min)
        os.makedirs(self.cut_dir, exist_ok=True)
        saturation.save(path, {"sig": sig, "scan": res, "at": now()})
        return res

    def declip_cut(self, key, cut, lean):
        """The cut's sound, listened to for clipped peaks. With a choice of channels the clean one is used; with no
        choice the tools are tried on a sample and the best one that passes repairs the whole talk (saturation.rescue);
        a talk still SATURATED after all that gets Studio Sound at saturated_studio_sound_percent - the last resort.
        Returns the talk's record (before, what was done, "last_resort", a note)."""
        before = saturation.scan(lean)
        before.pop("tens", None) if not before["some"] else None
        rec = {"before": before, "some": before["some"], "saturated": before["saturated"], "repaired": False,
               "last_resort": False}
        if not before["some"]:
            rec["note"] = "no clipping"
            return rec
        what, steps = saturation.describe(before), []
        if cut.get("declip") == "off" or not self.cfg.get("declip", True):
            steps.append("repair off")
        else:
            ch = saturation.pick_channel(before)
            if ch:
                af, clean = ch
                tmp = lean + ".channel.mp4"
                subprocess.run([media.tool("ffmpeg"), "-v", "error", "-y", "-i", lean, "-map", "0:v:0", "-map", "0:a:0",
                                "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp],
                               check=True)
                os.replace(tmp, lean)
                now_ = saturation.scan(lean)
                steps.append("the %s channel is clipped, the %s one is not: the clean one on both (%s)" % (
                    "right" if clean == 0 else "left", "left" if clean == 0 else "right", saturation.describe(now_)))
                rec["after"] = now_
            cur = rec.get("after") or before
            if cur["some"]:
                self.set(key, "cut", "running", label="trying the repair tools on %d s of the clipped sound" %
                         saturation.SAMPLE_SECONDS, percent=99)
                folder = os.path.join(self.cut_dir, self.fname(key) + ".sound-trial")
                res = saturation.rescue(lean, folder, self.cfg, before=cur, log=lambda m: self.note("%s: %s" % (key, m)))
                rec["rescue"] = {k: v for k, v in res.items() if k != "before"}
                steps.append(res.get("why") or "")
                if res.get("saved"):
                    rec["after"], rec["repaired"] = res["after"], True
                rec["trial_folder"] = self.fname(key) + ".sound-trial"
            else:
                rec["repaired"] = True
        final = rec.get("after") or before
        if final["saturated"]:
            rec["last_resort"] = True
            steps.append("still SATURATED: Studio Sound %s%% in Descript (the last resort)" %
                         self.cfg.get("saturated_studio_sound_percent", 100))
        elif rec["repaired"]:
            steps.append("saved: the normal Studio Sound %s%%" % self.cfg.get("studio_sound_percent", 80))
        rec["note"] = what + " - " + "; ".join(x for x in steps if x)
        self.note("%s: %s" % (key, rec["note"]))
        return rec

    def look_all(self, keys=None, force=False, report=None):
        """The picture plans of these cuts (default: all), several talks at once - the machine is free while the
        factory works ("go ham"). Cached plans are reused. report(key, plans, deck, notes, sound notes) per talk."""
        cuts = {c["key"]: c for c in self.cuts()["cuts"]}
        keys = [k for k in (keys or list(cuts)) if k in cuts]
        workers = int(self.cfg.get("look_parallel") or max(2, (os.cpu_count() or 4) // 4))

        def one(key):
            c = cuts[key]
            try:
                padded, _, _ = self.padded(key, self.talk_parts(key, c))
                plans, deck, notes = self.picture_for(key, c, padded, force=force,
                                                      on_progress=lambda label, pct=None: None)
                _, snd = self.sound_for(c, padded)
                clip = self.clipping_for(key, c, padded, force=force)
                snd = snd + ["sound: " + saturation.describe(clip) + (
                    " - the repair tools will be tried on a sample (Studio Sound %s%% only if none helps)" %
                    self.cfg.get("saturated_studio_sound_percent", 100) if clip.get("some") else "")]
                if report:
                    report(key, plans, deck, notes, snd)
            except Exception as ex:
                self.note("%s: the picture pass failed: %s" % (key, ex))
                traceback.print_exc()
        if keys:
            self.note("looking at the slides of %d talk(s), %d at a time" % (len(keys), workers))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, keys))

    def picture_extra(self, key, plan, part, size):
        """compose()'s slides + colour for one part from its plan, the deck pages rendered at the output size."""
        if not plan:
            return {}
        extra = {}
        if plan.get("colour"):
            extra["colour"] = plan["colour"]
        segs = [s for s in plan.get("pages") or [] if s["t1"] > part["start"] and s["t0"] < part["end"]]
        if segs and plan.get("deck"):
            folder = os.path.join(self.cut_dir, "_deck", re.sub(r"[^\w.-]+", "_", key), "%s_%d" % (part["rec"], int(part["start"])))
            imgs = picture.page_images(plan["deck"], [picture.variant(s) for s in segs], size[0], size[1], folder)
            lst, expr = picture.slide_track(plan, part["start"], part["end"], imgs, folder)
            if lst:
                extra["slides"] = (lst, expr)
        return extra

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
        if self.is_sound(rid):
            for step in ("frames", "proxy"):
                if not done(step):
                    self.rset(rid, step, "done", note="sound only")
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
                if isinstance(c.get("ends"), (dict, str)):
                    sig += "+" + hashlib.sha1(json.dumps(c["ends"], sort_keys=True).encode()).hexdigest()[:6]
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
        for leftover in (os.path.join(self.cut_dir, self.fname(key) + ".mp4"), os.path.join(self.out, "_descript", self.fname(key) + ".mp4")):
            if os.path.exists(leftover):
                os.remove(media.safe_target(leftover))
        kept = t.get("composition_id")
        if kept:
            t.setdefault("broken_compositions", []).append(kept)
        for k in ("composition_id", "composition_name", "finished", "trim", "error", "current", "stereo", "resolution",
                  "zoom", "in_clip", "selfcheck_ok", "problems", "doubts"):
            t.pop(k, None)
        if key in self.reviews():
            self.set_reviewed(key, False)               # a new cut is a new video: reviewed again
        t["steps"] = {}
        t.setdefault("redone", []).append({"at": now(), "why": why, "composition_id": kept})
        self.note("%s: %s - doing it again" % (key, why))

    # ---- sharp starts and ends (ends.py) ----------------------------------------------------------------------------
    # Marek, 2026-11-27: "I would like you to work extensively now to ensure the beginnings and the ends of talks are
    # super sharp and production-ready ... Q&A need to always be outed ... I just don't want someone to finish mid
    # sentence" - "full auto, so sharp cuts with guardrails".
    def transcript_segments(self, rid):
        return (analyse.load(self.rec_dir(rid), "transcript.json") or {}).get("segments") or []

    def ends_for(self, key, cut=None, force=False):
        """The talk's sharp edges: the ones confirmed in cuts.json ("ends": {"start", "end", "by", "why"}) or else the
        rules' (ends.refine, cached in <work folder>/ends.json by the cut's coarse parts). None when turned off."""
        cut = cut or next((c for c in self.cuts()["cuts"] if c["key"] == key), None)
        if not cut or not self.cfg.get("sharp_ends", True) or cut.get("ends") == "off":
            return None
        path = os.path.join(self.dir, "ends.json")
        sig = signature(cut["parts"])
        with self.lock:
            cache = ends.load(path) or {}
        r = cache.get(key)
        if not r or r.get("sig") != sig or force:
            talk = next((x for x in events.talks(self.ev) if x["key"] == key), None)
            names = segment._names(talk["name"]) if talk else []
            with self.ends_lock:
                r = ends.refine(self, key, cut, names, log=self.note)
            r.update(sig=sig, at=now())
            with self.lock:
                cache = ends.load(path) or {}
                cache[key] = r
                ends.save(path, cache)
        conf = cut.get("ends")
        if isinstance(conf, dict) and conf.get("start") is not None and conf.get("end") is not None:
            r = dict(r, start=segment.parse_stamp(conf["start"]), end=segment.parse_stamp(conf["end"]),
                     by=conf.get("by") or "claude", why=conf.get("why") or "", sure=True, flags=[],
                     first=conf.get("first") or r.get("first"), last=conf.get("last") or r.get("last"))
        elif conf == "rules":
            r = dict(r, sure=True, flags=[], by="rules (taken as they are)")
        return r

    def talk_parts(self, key, cut=None):
        """The parts the talk is cut from: the coarse ones with the sharp edges (ends_for) on the first and last."""
        cut = cut or next((c for c in self.cuts()["cuts"] if c["key"] == key), None)
        if cut is None:
            return [dict(p) for p in self.state["talks"][key]["parts"]]
        r = self.ends_for(key, cut)
        if not r:
            return [dict(p) for p in cut["parts"]]
        p0, p1 = cut["parts"][0], cut["parts"][-1]
        if not (r["start"] < p0["end"] and r["end"] > p1["start"] and (len(cut["parts"]) > 1 or r["end"] - r["start"] > 60)):
            return [dict(p) for p in cut["parts"]]
        return ends.apply(cut, r)

    def ends_all(self, keys):
        """Close listening at the edges of these talks (one at a time: the GPU), before anything is cut."""
        out = {}
        cuts = {c["key"]: c for c in self.cuts()["cuts"]}
        for k in keys:
            if k in cuts:
                try:
                    out[k] = self.ends_for(k, cuts[k])
                except Exception as ex:
                    self.note("%s: listening at the edges failed: %s" % (k, ex))
                    traceback.print_exc()
        return out

    def ends_brief(self, keys=None):
        """ends_brief.md: per talk the edges found, why, the sentences around them - for Claude to confirm or move
        ("ends" in cuts.json) before the run. Returns its path."""
        cuts = {c["key"]: c for c in self.cuts()["cuts"]}
        keys = [k for k in (keys or list(cuts)) if k in cuts]
        lines = ["# Sharp starts and ends - %s" % self.ev["title"], "",
                 "Per talk: where it starts and ends now, why, and the sentences around both edges (recording time; "
                 ">> marks the chosen sentence). Confirm or move each in cuts.json: \"ends\": {\"start\": \"0:31:12.4\", "
                 "\"end\": \"0:52:40.1\", \"by\": \"claude\", \"why\": \"...\"} - the start on the speaker's first words "
                 "to the room (not the MC, not the mic check), the end right after their last sentence before the Q&A, "
                 "finished, ideally the closing thank-you. A FLAGGED talk stops before cutting until its edges are "
                 "confirmed.", ""]
        for k in keys:
            r = self.ends_for(k, cuts[k])
            if not r:
                continue
            p0, p1 = cuts[k]["parts"][0], cuts[k]["parts"][-1]
            lines += ["## %s%s" % (k, "  - FLAGGED: " + "; ".join(r["flags"]) if r.get("flags") else ""), "",
                      "- start %s (%s, coarse %s, %+.1f s): \"%s\" - %s" % (
                          "%s.%d" % (segment.stamp(int(r["start"])), int(r["start"] * 10) % 10), p0["rec"], segment.stamp(p0["start"]), r["start"] - p0["start"],
                          r.get("first", "")[:90], r.get("why_start", "")),
                      "- end %s (%s, coarse %s, %+.1f s): \"%s\" - %s" % (
                          "%s.%d" % (segment.stamp(int(r["end"])), int(r["end"] * 10) % 10), p1["rec"], segment.stamp(p1["end"]), r["end"] - p1["end"],
                          r.get("last", "")[-90:], r.get("why_end", ""))] + \
                ["- note: " + n for n in r.get("notes") or []] + [""]
            for what, ctx in (("around the start", r.get("around_start") or []), ("around the end", r.get("around_end") or [])):
                lines.append("%s:" % what)
                lines += ["    %s %s.%d %s" % (">>" if c[2] else "  ", segment.stamp(int(c[0])), int(c[0] * 10) % 10, c[1])
                          for c in ctx]
                lines.append("")
        path = os.path.join(self.dir, "ends_brief.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def padded(self, key, parts=None):
        """The talk's parts (or these parts of its cut) with pad_before_seconds before the first and pad_after_seconds
        after the last, never into another talk's own cut on the same recording, nor past the recording.
        Returns (parts, before, after)."""
        parts = [dict(p) for p in (parts if parts is not None else self.talk_parts(key))]
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
        machine.swap_in(path + ".tmp", path)

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
        machine.swap_in(tmp, self.reviews_path)

    def sound_problem(self, lean):
        """Why the cut can't be a talk on YouTube by its sound (none at all, or silent for over half of it), or None."""
        info = media.probe(lean)
        if not info["channels"]:
            return "the cut has no sound (the recording has none here)"
        quiet = sum(n for _, n in selfcheck.silences(lean, least=10.0))
        if info["duration"] and quiet > 0.5 * info["duration"]:
            return "the cut is silent for %.0f of its %.0f s (a muted mic?)" % (quiet, info["duration"])
        return None

    def fname(self, key):
        """The talk key as a file name on Windows and macOS: no / \\ : * ? " < > | (a name like "Q&A: Ann / Bob")."""
        return re.sub(r'[\\/:*?"<>|]+', "", key).strip() or "talk"

    def srt_path(self, key):
        return os.path.join(self.srt_dir, re.sub(r"[^\w.-]+", "_", "%s_%s.srt" % (self.ev["id"], key)))

    def autopilot(self):
        """The event runs on its own to YouTube: cuts.json "autopilot": true (Claude sets it when asked to ship an
        event), or settings autopilot: true."""
        v = self.cuts().get("autopilot")
        return bool(self.cfg.get("autopilot")) if v is None else bool(v)

    def approve(self, keys=None, by="claude"):
        """Gate 1 by Claude on autopilot: the named cuts (all when none named) approved, with who and when. A cut
        whose sharp ends are still flagged (not confirmed in "ends") is not approved - it is named, never guessed.
        Returns (approved, held: {key: why})."""
        data = segment.annotate(self, self.cuts())       # with the lineup checks' flags (a swapped slot ...)
        ok, held, go = [], {}, []
        for c in data["cuts"]:
            if keys and c["key"] not in keys:
                continue
            if c.get("approved"):
                ok.append(c["key"])
                continue
            if not keys and (c.get("flags") or c.get("confidence") == "low"):
                held[c["key"]] = "; ".join(c.get("flags") or []) or "low confidence - not sure it is this talk"
                continue
            confirmed = isinstance(c.get("ends"), dict) or c.get("ends") in ("rules", "off")
            rec = None if confirmed or not self.cfg.get("sharp_ends", True) else self.ends_for(c["key"], c)
            if rec and rec.get("flags"):
                held[c["key"]] = "; ".join(rec["flags"])
                continue
            go.append(c["key"])
        data = self.cuts()                               # read again: the listening took minutes, edits since stay
        for c in data["cuts"]:
            if c["key"] in go and not c.get("approved"):
                c["approved"], c["approved_by"], c["approved_at"] = True, by, now()
                ok.append(c["key"])
        self.save_cuts(data)
        return ok, held

    def final_path(self, key):
        safe = re.sub(r'[\\/:*?"<>|]+', "", key)
        return os.path.join(self.final_dir, "%s - %s.mp4" % (safe, self.ev["title"]))

    # ---- 4. one talk: cut -> Descript -> finished ------------------------------------------------------------------
    def process(self, key):
        t, cfg = self.state["talks"][key], self.cfg
        done = lambda s: (t["steps"].get(s) or {}).get("status") == "done"
        lean = os.path.join(self.cut_dir, self.fname(key) + ".mp4")
        raw = os.path.join(self.out, "_descript", self.fname(key) + ".mp4")
        final = self.final_path(key)
        srt_path = self.srt_path(key)
        progress = lambda step: (lambda label, percent=None: self.set(key, step, "running", label=label, percent=percent))
        try:
            with self.lock:
                t["error"] = None
            if not done("cut"):
                with self.render_sem:
                    self.set(key, "cut", "running", label="checking disk space", percent=0)
                    self.ensure_room(key, "cut")
                    st = self.stereo_for({"parts": t["parts"]})
                    cut_cfg = next((c for c in self.cuts()["cuts"] if c["key"] == key), {})
                    self.set(key, "cut", "running", label="listening closely at the start and the end", percent=0)
                    edges = self.ends_for(key, cut_cfg) if cut_cfg else None
                    if edges and edges.get("flags"):
                        msg = ("stopped before Descript - the start/end is not sure: %s. Read ends_brief.md "
                               "(python -m live_factory ends <event>) and confirm the edges in cuts.json (\"ends\": "
                               "{\"start\": .., \"end\": .., \"by\": \"claude\", \"why\": ..}), or \"ends\": \"rules\" to take "
                               "them as they are" % "; ".join(edges["flags"]))
                        self.set(key, "cut", "failed", error=msg)
                        raise RuntimeError(msg)
                    use = self.talk_parts(key, cut_cfg) if cut_cfg else [dict(p) for p in t["parts"]]
                    padded, before, after = self.padded(key, use)
                    crop, zoom_note = None, None
                    if cfg.get("zoom_black_bars", True):
                        self.set(key, "cut", "running", label="looking for black bars around the slides", percent=0)
                        p0 = t["parts"][0]
                        crop, zoom_note = media.black_bars(self.rec_path(p0["rec"]), p0["start"], p0["end"],
                                                           min_zoom_percent=float(cfg.get("zoom_min_percent", 5)))
                    plans, deck, pic_notes = self.picture_for(key, cut_cfg, padded, on_progress=progress("cut"))
                    sounds, snd_notes = self.sound_for(cut_cfg, padded)
                    if any(pl and pl.get("shown", 0) >= 0.9 for pl in plans):
                        crop, zoom_note = None, None          # the deck pages fill the frame: no zoom wanted
                    self.set(key, "cut", "running", label="cutting", percent=0)
                    parts = []
                    for p, pl, snd in zip(padded, plans, sounds):
                        src = self.rec_path(p["rec"])
                        pi = (self.state["recordings"][p["rec"]].get("probe") or {})
                        size = media.out_size(pi.get("width") or 0, pi.get("height") or 0, 1080, crop)
                        extra = self.picture_extra(key, pl, p, size)
                        if snd:
                            extra["sound"] = snd
                        parts.append((src, p["start"], p["end"], extra))
                    media.cut(parts, lean, st.get("af"), on_progress=progress("cut"), cq=int(cfg.get("cut_crf") or 18),
                              crop=crop)
                    self.set(key, "cut", "running", label="listening for clipped sound", percent=99)
                    sat = self.declip_cut(key, cut_cfg, lean)
                    check = None
                    edge_problems, edge_notes = [], []
                    if edges:                                 # the cut's own first and last words, heard again
                        self.set(key, "cut", "running", label="hearing the cut's first and last words", percent=99)
                        with self.ends_lock:
                            edge_problems, edge_notes = ends.verify(lean, edges, cfg)
                        check = {"problems": edge_problems, "notes": edge_notes, "at": now()}
                    if any(x[3] for x in parts):              # deck pages or separate sound laid in: check the file
                        self.set(key, "cut", "running", label="checking the picture and the sound of the cut", percent=99)
                        out_info = media.probe(lean)
                        problems, cnotes = precheck.check(lean, [x[:3] for x in parts], [x[3] for x in parts],
                                                          (out_info["width"], out_info["height"]),
                                                          os.path.join(self.cut_dir, self.fname(key) + ".check.jpg"), crop=crop)
                        check = {"problems": edge_problems + problems, "notes": edge_notes + cnotes,
                                 "sheet": self.fname(key) + ".check.jpg", "at": now()}
                    with self.lock:
                        t["check"] = check
                        t["picture"] = {"deck": deck, "notes": pic_notes,
                                        "shown": [pl.get("shown") if pl else None for pl in plans],
                                        "night": [(pl.get("night") or {}).get("on") if pl else None for pl in plans]}
                        t["sound"] = {"notes": snd_notes, "separate": any(sounds)}
                        t["saturation"] = sat
                        t["ends"] = edges
                    info = media.probe(lean)
                    core = sum(p["end"] - p["start"] for p in use)
                    with self.lock:
                        t["stereo"], t["probe"], t["zoom"] = st, info, {"crop": crop, "note": zoom_note}
                        # where the judged talk sits in the clip, for trimming the extra footage by hand in Descript
                        t["in_clip"] = {"start": round(before, 1), "end": round(before + core, 1), "before": round(before, 1),
                                        "after": round(after, 1)}
                        t["resolution"] = media.pick_resolution(info["width"], info["height"], cfg["max_resolution"])
                    shown = [x for x in t["picture"]["shown"] if x]
                    self.set(key, "cut", "done", sig=t["cut_sig"], note="%s; the talk runs %s-%s in the clip; %s%s%s%s%s" % (
                        segment.stamp(info["duration"]), segment.stamp(before), segment.stamp(before + core), st["note"],
                        "; " + zoom_note if crop else "",
                        "; deck pages on %d%% of it" % (100 * max(shown)) if shown else "",
                        "; separate sound" if t["sound"]["separate"] else "",
                        "; " + sat["note"] if sat.get("some") else "") + (
                        "; starts \"%s\", ends \"%s\"" % (edges.get("first", "")[:40], edges.get("last", "")[-40:])
                        if edges else ""))
            if not done("check"):
                cut_cfg = next((c for c in self.cuts()["cuts"] if c["key"] == key), {})
                chk = t.get("check")
                if not done("upload"):                   # no sound to speak of: never paid for in Descript
                    mute = self.sound_problem(lean)
                    if mute:
                        self.set(key, "check", "failed", error=mute)
                        raise RuntimeError("stopped before Descript - " + mute)
                if done("upload"):
                    self.set(key, "check", "done", note="uploaded before the check existed")
                elif cut_cfg.get("precheck") == "off":
                    self.set(key, "check", "done", note="check turned off for this talk (\"precheck\": \"off\")")
                elif chk and chk.get("problems"):
                    self.set(key, "check", "failed", error="; ".join(chk["problems"][:4]))
                    raise RuntimeError("stopped before Descript - the cut is not right: %s (look at cuts/%s; fix the "
                                       "cut or the plan and redo it, or set \"precheck\": \"off\")" % (
                                           "; ".join(chk["problems"][:3]), chk.get("sheet")))
                else:
                    self.set(key, "check", "done", note="; ".join((chk or {}).get("notes") or []) or
                             "nothing laid in - the recording as it is")
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
                        with self.lock:
                            t["composition_id"], t["composition_name"] = existing[comp_name]["id"], comp_name
                        self.set(key, "upload", "done", note="already in Descript, reused")
            if not done("upload") and not os.path.exists(lean):    # the cut was cleaned away: cut it again
                with self.lock:
                    t["steps"].pop("cut", None)
                    t["steps"].pop("check", None)
                raise RuntimeError("the cut file is gone - it is cut again on the retry")
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
                    with self.lock:
                        t.setdefault("broken_compositions", []).append(cid)
                    raise RuntimeError("Descript got no usable video from the upload (%.0f s)" % (comp.get("duration") or 0))
                with self.lock:
                    t["composition_id"], t["composition_name"] = cid, comp_name
                self.set(key, "upload", "done", note=comp_name if comp_name != key else None)
            pid, cid = self.state["project_id"], t["composition_id"]
            # credit guard 2: never pay for the same edit twice (the ledger, or a composition already shorter)
            resume_job = None
            if not done("edit"):
                paid = self.ledger().get(cid)
                if paid and paid.get("done") is False and paid.get("job_id"):
                    resume_job, paid = paid["job_id"], None   # started before a crash: wait on that job, pay nothing new
                if not paid and not resume_job:
                    pr = d.project(pid)
                    comp = next((c for c in pr.get("compositions", []) if c["id"] == cid), {})
                    mf = pr.get("media_files") or {}
                    mine = [v for n, v in mf.items() if n == key or os.path.splitext(n)[0] == comp_name or
                            n.startswith(comp_name + ".") or n.startswith(comp_name + " (")]
                    media_len = max([(v or {}).get("duration") or 0 for v in mine] or [0])
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
                if not resume_job:
                    resume_job = d.start_edit(pid, cid, self.edit_prompt(key, comp_name), on_progress=progress("edit"))
                    self.record_edit_start(cid, key, resume_job)
                j = d.wait(resume_job, 20, progress("edit"))
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
            # GATE 2: Marek looks at the edited talk in Descript before anything is exported - on autopilot the
            # factory's own check of the final (selfcheck, below) takes its place
            reviewed = self.reviews().get(key)
            paused_by_me = str(self.state.get("edits_paused") or "").split("'s edit")[0] == key
            if not reviewed and self.autopilot() and not paused_by_me:
                reviewed = "autopilot - checked by the factory after the export"
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
                self.set(key, "download", "running", label="checking disk space")
                self.ensure_room(key, "export")
                self.set(key, "download", "running")
                os.makedirs(os.path.dirname(raw), exist_ok=True)
                with requests.get(t["steps"]["publish"]["download_url"], stream=True, timeout=600) as r:
                    if 400 <= r.status_code < 500:              # the link expired: publish again (free) on the retry
                        with self.lock:
                            t["steps"].pop("publish", None)
                        raise RuntimeError("Descript's download link answered %d - published again on the retry"
                                           % r.status_code)
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
            if not done("proofread"):
                self.proofread_step(key, srt_path)
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
            if not done("selfcheck"):
                self.set(key, "selfcheck", "running", label="the factory looks at the final: edges, sound, picture")

                def heard(f, rec, c):
                    with self.ends_lock:
                        return ends.verify(f, rec, c)
                try:
                    problems, notes = selfcheck.check(final, t, cfg, t.get("ends"), verify=heard,
                                                      lean=lean if os.path.exists(lean) else None)
                except Exception as ex:                  # noqa: BLE001 - never leaves a talk unfinished
                    problems, notes = ["the factory's own check could not run (%s)" % str(ex)[:200]], []
                cut_doubt = (next((c for c in self.cuts()["cuts"] if c["key"] == key), {}) or {}).get("check")
                doubts = ([cut_doubt] if cut_doubt else []) + selfcheck.doubts_of(notes)
                with self.lock:
                    t["selfcheck_ok"] = not problems
                    t["problems"] = problems
                    t["doubts"] = doubts
                self.set(key, "selfcheck", "done", problems=problems, notes=notes, doubts=doubts,
                         note=("PROBLEMATIC - not for YouTube: " + "; ".join(problems)) if problems else
                         ("shipped, please check: " + "; ".join(doubts)) if doubts else
                         "; ".join(notes[:3]) or "all good")
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
        room = youtube.budget(self.ev, self.cfg, who, talk)
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
    def edit_prompt(self, key, comp_name):
        """The AI editor's prompt. A talk still saturated after the repair tools gets Studio Sound at
        saturated_studio_sound_percent (the last resort); a repaired one the normal studio_sound_percent."""
        vals = dict(self.cfg)
        if (self.state["talks"][key].get("saturation") or {}).get("last_resort"):
            vals["studio_sound_percent"] = self.cfg.get("saturated_studio_sound_percent", 100)
        return self.cfg["edit_prompt"].format(composition=comp_name, **vals)

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
        the link to its thumbnail - ready to paste. One writer at a time (both talk threads call it)."""
        with self.plock:
            return self._write_youtube()

    def _write_youtube(self):
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

    def find_leftovers(self, d=None):
        """Compositions failed Descript imports leave behind: a cancelled import or a 6 s upload still creates
        "<talk>", and an upload that looked empty can fill in later as a full-length UNEDITED duplicate with the
        same name (LLMday Austin: 5 empty ones + Rodney's 38 min twin). Descript's API can't delete a composition,
        so they're listed for Marek to delete by hand - by length, since the name matches the real one. The talk's
        real composition and a redo's earlier version (kept on purpose) are never listed."""
        if not self.state.get("project_id"):
            return []
        try:
            comps = (d or Descript()).project(self.state["project_id"]).get("compositions", [])
        except Exception as ex:
            self.note("could not check Descript for leftover compositions: %s" % ex)
            return []
        keep = {t.get("composition_id") for t in self.state["talks"].values()}
        keep |= {r.get("composition_id") for t in self.state["talks"].values() for r in t.get("redone") or []}
        talks = list(self.state["talks"])
        left = []
        for c in comps:
            if c["id"] in keep or not any(c["name"] == k or c["name"].startswith(k + " (") for k in talks):
                continue
            secs = round(c.get("duration") or 0, 1)
            left.append({"name": c["name"], "id": c["id"], "seconds": secs,
                         "kind": "empty" if secs < MIN_TALK_SECONDS else "unedited duplicate"})
        with self.lock:
            if left:
                self.state["leftover_compositions"] = left
            else:
                self.state.pop("leftover_compositions", None)
            self.save()
        if left:
            self.note("delete in Descript (leftovers of failed uploads, the API can't delete them): " +
                      "; ".join("%s - the %s one (%s)" % (c["name"], c["kind"], segment.stamp(c["seconds"])) for c in left))
        return left

    @staticmethod
    def lasting(err):
        """An error that trying again won't fix (the same cut fails the same check; no credits; paused edits)."""
        err = err or ""
        return ("402" in err or "edits paused" in err or "free (keeping" in err or "stopped before Descript" in err
                or "has no sound" in err or "is silent" in err or "left out until their cut is fixed" in err)

    def process_with_retries(self, key, attempts=3, pause=120):
        t = self.state["talks"][key]
        for n in range(1, attempts + 1):
            self.process(key)
            err = t.get("error") or ""
            if not err or n == attempts or self.lasting(err):    # the same cut fails the same check again
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
        held = {c["key"] for c in self.cuts()["cuts"] if any(c["key"] in p for p in bad)}
        if bad and not held:
            raise SystemExit("cuts.json has errors - fix them first: " + "; ".join(bad))
        if bad:                                          # the talks with a wrong cut wait; the others go on
            self.note("left out until their cut is fixed: %s" % "; ".join(bad))
            with self.lock:
                self.state["cut_errors"] = {k: "; ".join(p for p in bad if k in p)[:300] for k in held}
        else:
            self.state.pop("cut_errors", None)
        self.sync_talks()
        redone = self.apply_redo_requests()
        keys = [k for k in self.sync_talks() if not self.state["talks"][k].get("finished") and (not only or k in only)
                and k not in held]
        if redone:
            self.note("redo: %s - cut again and uploaded as a fresh composition" % ", ".join(redone))
        if keys:
            self.ensure_space("cutting and exporting %d talk(s)" % len(keys))
        if not keys:
            if self.autopilot():                         # nothing new to cut: what the quota left goes up now
                self.youtube_pass()
                self.ship_report()
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
        # the picture plans first, many talks at once (the cut step then finds them made)
        uncut = [k for k in keys if (self.state["talks"][k]["steps"].get("cut") or {}).get("status") != "done"]
        if uncut and self.cfg.get("sharp_ends", True):
            self.note("listening closely at the start and the end of %d talk(s)" % len(uncut))
            self.ends_all(uncut)
            self.ends_brief(uncut)
        if uncut and picture.available() is None:
            self.look_all(uncut)
        with ThreadPoolExecutor(max_workers=max(1, int(self.cfg.get("parallel_talks") or 2))) as pool:
            list(pool.map(self.process_with_retries, keys))
        self.find_leftovers()
        self.write_youtube()
        if self.autopilot():
            self.youtube_pass()
            try:
                self.ship_report()
            except Exception as ex:                      # noqa: BLE001
                self.note("ship report not written: %s" % ex)
        waiting = [k for k in keys if (self.state["talks"][k]["steps"].get("review") or {}).get("status") == "waiting"]
        self.note("run finished" + (" - %d talk(s) waiting for your review in Descript" % len(waiting) if waiting else ""))


    # ---- the transcript proofread before it goes up as captions (proofread.py, Marek 2026-11-27) ------------------
    def proofread_step(self, key, srt_path):
        """Glossary + Claude fix the misheard words of the talk's Descript transcript, in the talk's context. The
        Descript original stays in srt/descript/, the fixes go to srt/proofread/<file>.json. Never fails the talk:
        without Claude the glossary fixes stand and the step is "partial" - tried again before the upload."""
        self.set(key, "proofread", "running", label="Claude reads the transcript")
        keep = os.path.join(self.srt_dir, "descript", os.path.basename(srt_path))
        if not os.path.exists(keep):
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            shutil.copy2(srt_path, keep)
        talk = next((x for x in events.talks(self.ev) if x["key"] == key), None)
        try:
            rep = proofread.proofread_file(srt_path, proofread.talk_context(self.ev, talk), self.cfg)
        except Exception as ex:                          # noqa: BLE001 - the transcript as Descript wrote it stands
            rep = {"fixes": [], "glossary": 0, "claude": 0, "partial": True, "error": str(ex)[:300]}
        os.makedirs(os.path.join(self.srt_dir, "proofread"), exist_ok=True)
        with open(os.path.join(self.srt_dir, "proofread", os.path.basename(srt_path) + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(rep, f, indent=1, ensure_ascii=False)
        note = "%d fixes (%d glossary, %d Claude)" % (len(rep["fixes"]), rep["glossary"], rep["claude"])
        if rep["partial"]:
            note += " - glossary only: %s" % (rep["error"] or "Claude off in settings")
        self.set(key, "proofread", "done", fixes=len(rep["fixes"]), partial=rep["partial"], note=note)
        return rep

    def proofread_pending(self):
        """Finished talks whose proofread was only partial (Claude not reachable then): Claude again, before the
        captions go up."""
        for k, t in sorted(self.state["talks"].items()):
            st = t["steps"].get("proofread") or {}
            if t.get("finished") and st.get("partial") and os.path.exists(self.srt_path(k)):
                talk = next((x for x in events.talks(self.ev) if x["key"] == k), None)
                try:
                    rep = proofread.proofread_file(self.srt_path(k), proofread.talk_context(self.ev, talk), self.cfg)
                except Exception as ex:                  # noqa: BLE001
                    rep = {"fixes": [], "claude": 0, "partial": True, "error": str(ex)[:300]}
                if not rep["partial"]:
                    self.set(k, "proofread", "done", fixes=st.get("fixes", 0) + len(rep["fixes"]), partial=False,
                             note="%s; Claude later: %d fixes" % (st.get("note", ""), rep["claude"]))

    def youtube_pass(self, keys=None):
        """The checked talks to the brand's YouTube channel (ytupload.event); each line to the log. Never fails the
        run: what is not up yet goes up on the next pass."""
        lines = []
        self.proofread_pending()                         # no caption goes up unproofread when Claude can be reached
        try:
            for line in ytupload.event(self, keys):
                self.note(line)
                lines.append(line)
        except Exception as ex:                          # noqa: BLE001
            self.note("youtube: stopped - %s" % ex)
            lines.append("youtube: stopped - %s" % ex)
        self.last_youtube = lines
        return lines

    def ship(self, again_after=1800, rounds=2, quota_days=3):
        """The whole night in one job (Marek 2026-11-28: "I want to be able to put the laptop down for the night and
        make sure it's not going to stop mid run"), after Claude made the judgment calls (cuts, ends, decks):
        run() - cut, Descript, export, proofread, selfcheck, upload - then
          - talks that failed for a passing reason (the network, Descript busy) again after `again_after` s, `rounds`
            times;
          - the YouTube day's limit: wait for the next quota day (08:05 UTC) and go on, up to `quota_days` days;
          - the ship report with the materials to ask for at the end ("missing.md" lines in it).
        Nothing in it waits for a person."""
        self.cuts_data_autopilot_check()
        self.run()
        for n in range(rounds):
            again = [k for k, t in self.state["talks"].items() if t.get("error") and not self.lasting(t["error"])
                     and not t.get("finished")]
            if not again:
                break
            self.note("trying %d talk(s) again in %d min: %s" % (len(again), again_after // 60, ", ".join(again)))
            WAIT(again_after)
            with self.lock:
                for k in again:
                    t = self.state["talks"][k]
                    for step in list(t["steps"]):
                        if (t["steps"][step] or {}).get("status") in ("running", "failed"):
                            del t["steps"][step]
                    t.update(error=None, retry=None, current=None)
                self.save()
            self.run(only=again)
        for day in range(quota_days):
            if not any("upload limit" in l for l in getattr(self, "last_youtube", None) or []):
                break
            wake = datetime.datetime.now(datetime.timezone.utc).replace(hour=8, minute=5, second=0, microsecond=0)
            if wake <= datetime.datetime.now(datetime.timezone.utc):
                wake += datetime.timedelta(days=1)
            secs = (wake - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            self.note("YouTube's day is full - waiting %.1f h for the next one, then uploading the rest" % (secs / 3600))
            WAIT(secs)
            self.youtube_pass()
        try:
            return self.ship_report()
        except Exception as ex:                          # noqa: BLE001
            self.note("ship report not written: %s" % ex)

    def cuts_data_autopilot_check(self):
        """ship is the autopilot's own job: it turns autopilot on for the event if it isn't."""
        if not self.autopilot():
            data = self.cuts()
            data["autopilot"] = True
            self.save_cuts(data)

    def ship_report(self):
        """<work folder>/ship report.md: what is on YouTube and when it goes public, what is missing and why, what is
        problematic - the base of Claude's postmortem (Marek 2026-11-27)."""
        yt = ytupload.load(self)
        data = self.cuts()
        cuts = {c["key"]: c for c in data["cuts"]}
        skipped = {s.get("key"): s.get("why") or s.get("reason") or "" for s in data.get("skipped") or []}
        try:
            when, note = ytupload.release_at(self.ev, self.cfg)
            release = "%s UTC (5 weeks after the event, 09:00 local)%s" % (when.strftime("%Y-%m-%d %H:%M"),
                                                                         " - " + note if note else "")
        except ValueError as ex:
            release = "unknown: %s" % ex
        lines = ["# %s - ship report" % self.ev["title"], "",
                 "Written %s. The videos go public %s." % (now()[:16].replace("T", " "), release), ""]
        rows = {"on YouTube": [], "please check": [], "problematic": [], "stopped": [], "not cut": [], "missing": []}
        for talk in events.talks(self.ev):
            k, t, rec = talk["key"], self.state["talks"].get(talk["key"]), yt.get(talk["key"]) or {}
            if rec.get("id") and t and t.get("doubts"):
                rows["please check"].append("- **%s** - %s - shipped on the benefit of the doubt: %s" % (
                    k, rec.get("url"), "; ".join(t["doubts"])[:300]))
            if rec.get("id"):
                extras = [x for x in ("thumbnail", "captions", "playlist") if rec.get(x) is not True]
                rows["on YouTube"].append("- **%s** - %s - %s%s%s" % (
                    k, rec.get("url"), rec.get("privacy"),
                    (", public %s UTC" % rec["publish_at"][:16].replace("T", " ")) if rec.get("publish_at") else "",
                    (" - still to add: " + ", ".join(extras)) if extras else ""))
            elif t and t.get("finished") and not t.get("selfcheck_ok"):
                rows["problematic"].append("- **%s** - %s" % (k, "; ".join(t.get("problems") or ["not checked"])))
            elif t and t.get("finished") and ytupload.shelved(self.cfg, self.ev["brand"]):
                rows["on YouTube"].append("- **%s** - finished, in the final videos folder (no upload: %s)" % (
                    k, ytupload.shelved(self.cfg, self.ev["brand"])))
            elif t and t.get("finished"):
                rows["stopped"].append("- **%s** - finished, not on YouTube yet%s" % (
                    k, (": " + rec["error"][:200]) if rec.get("error") else " (see the log)"))
            elif t and t.get("error"):
                rows["stopped"].append("- **%s** - %s" % (k, t["error"][:300]))
            elif k in cuts and not cuts[k].get("approved"):
                rows["not cut"].append("- **%s** - cut not approved%s" % (
                    k, (" (" + cuts[k]["reason"][:120] + ")") if cuts[k].get("reason") else ""))
            elif k in cuts:
                rows["stopped"].append("- **%s** - not through the factory yet" % k)
            else:
                rows["missing"].append("- **%s** - %s" % (k, skipped.get(k) or "not found in the recordings"))
        for head, items in rows.items():
            if items:
                lines += ["## %s (%d)" % (head[0].upper() + head[1:], len(items)), ""] + items + [""]
        ask = []                                         # the materials to ask for, once everything else is done
        for line in rows["missing"]:
            ask.append(line.replace("- **", "- the recording of **", 1))
        for line in rows["problematic"] + rows["stopped"]:
            if "no sound" in line or "silent" in line or "muted" in line:
                ask.append(line.replace("- **", "- another recording (with sound) of **", 1))
        for t in self.state["talks"].values():
            for n in (t.get("picture") or {}).get("notes") or []:
                if "another version" in n or "no deck" in n.lower():
                    ask.append("- the slides **%s** presented (%s)" % (t["key"], n[:80]))
        if ask:
            lines += ["## To ask for now (everything else is done)", ""] + ask + [""]
        pl = yt.get(ytupload.PLAYLIST) or {}
        if pl.get("url"):
            lines += ["## Playlist", "", "- %s%s" % (pl["url"], "" if pl.get("hooked") else
                      " - NOT on the website yet: youtube_playlist in the event's entry of home/metadata.yml"), ""]
        credits = sum(float((t["steps"].get("edit") or {}).get("credits") or 0) for t in self.state["talks"].values())
        lines += ["## Descript", "", "- AI credits used: %.1f" % credits]
        for c in self.state.get("leftover_compositions") or []:
            lines.append("- delete by hand in Descript: %s - the %s one (%s)" % (c["name"], c["kind"],
                                                                                segment.stamp(c["seconds"])))
        path = os.path.join(self.dir, "ship report.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path


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
            "problematic": {k: t.get("problems") for k, t in talks.items() if t.get("finished") and t.get("problems")},
            "flagged": {k: t["steps"]["qa"]["flags"] for k, t in talks.items() if (t["steps"].get("qa") or {}).get("flags")}}
