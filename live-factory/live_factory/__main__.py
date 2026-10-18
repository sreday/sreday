"""LIVE talk factory command line. Events are <brand>-<folder>, e.g. sreday-2026-london-q1.

  python -m live_factory add <event> <file or folder ...> [--day N] [--track T] [--in-place]
                                       register recordings (copied in from the card; --in-place reads them where they
                                       are). --track is the schedule track (1, 2 ...) or the room ("screen 2"); a day
                                       with one track needs only --day
  python -m live_factory analyse <event> [r1 r2 ...]   levels, Whisper transcript, stills, review copy (local, free)
  python -m live_factory brief <event>                 segment_brief_<track>.md for Claude's lineup match
  python -m live_factory draft <event> [--replace]     a first automatic guess at cuts.json (keeps approved cuts)
  python -m live_factory cuts <event> [file.json]      check cuts.json (or take this file as the new one) and list it
  python -m live_factory frame <event> <rec> <h:mm:ss> one still at that moment (prints the jpg's path)
  python -m live_factory run <event>                   approved cuts -> cut -> Descript edit; reviewed ones -> export
  python -m live_factory review <event> "<talk>"|--all  mark talks reviewed in Descript (or tick them in the app)
  python -m live_factory retry <event> "<talk>"        retry one talk from its failed step
  python -m live_factory redo <event> "<talk>" [why]   flag a talk to be cut again and re-uploaded (a fresh
                                                       composition "<talk> (redo N)") on the next run
  python -m live_factory unpause <event>               clear "edits paused" after checking the talk in Descript
  python -m live_factory publish                       update the Factory tab of sreday.com/factory now
  python -m live_factory youtube <event>               <event>/YouTube items.txt again
  python -m live_factory status <event>
  python -m live_factory serve [<event>]               the local app on http://localhost:8043, opened on the event
                                                       (an older app still running is replaced)
  python -m live_factory doctor                        check this machine
  python -m live_factory setup                         one-time setup (Descript token, launcher, check)
"""
import getpass
import json
import os
import socket
import stat
import sys

from . import events, machine, pipeline, segment


def print_doctor(check_descript=True):
    bad = 0
    for c in machine.doctor(pipeline.settings(), check_descript):
        mark = "OK  " if c["ok"] else ("FIX " if c["required"] else "opt ")
        bad += (not c["ok"] and c["required"])
        print("  %s %-24s %s" % (mark, c["name"], c["detail"]))
        if not c["ok"] and c["fix"]:
            print("       -> %s" % c["fix"])
    print("\n  ready to run." if not bad else "\n  %d thing(s) to fix before running." % bad)
    return bad


def setup():
    here = pipeline.HERE
    local = os.path.join(here, "settings.local.yml")
    if not os.path.exists(local):
        name = socket.gethostname()
        if sys.stdin.isatty():
            try:
                name = input("Name for this machine [%s]: " % name).strip() or name
            except EOFError:
                pass
        with open(local, "w", encoding="utf-8") as f:
            f.write("# this machine only (git-ignored). Anything from settings.yml can be overridden here.\n"
                    "machine_name: %s\n# work_root: D:/video-factory    # where the \"video factory - <event>\" folders go; default: the Desktop\n" % name)
        print("wrote " + local)
    if not machine.read_token() and sys.stdin.isatty():
        print("\nDescript API token (Descript > Settings > API tokens). Stored on this machine only, never in git.")
        tok = getpass.getpass("token (hidden): ").strip()
        if tok:
            print("stored as " + machine.store_token(tok))
    desk = machine.desktop()
    if os.name == "nt":
        path = os.path.join(desk, "LIVE talk factory.cmd")
        with open(path, "w", encoding="utf-8") as f:
            f.write('@start "" "%s"\r\n' % os.path.join(here, "run.cmd"))
    else:
        path = os.path.join(desk, "LIVE talk factory.command")
        with open(path, "w", encoding="utf-8") as f:
            f.write('#!/bin/bash\n"%s"\n' % os.path.join(here, "run.sh"))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("launcher: " + path + "\n\nchecking this machine:")
    return print_doctor()


def flag(argv, name, has_value=True):
    """Pull --name [value] out of argv; returns (value or True/None, argv without it)."""
    if name not in argv:
        return None, argv
    i = argv.index(name)
    if not has_value:
        return True, argv[:i] + argv[i + 1:]
    if i + 1 >= len(argv):
        raise SystemExit("%s needs a value" % name)
    return argv[i + 1], argv[:i] + argv[i + 2:]


def list_cuts(run, data):
    recs = run.state["recordings"]
    for c in data["cuts"]:
        parts = " + ".join("%s %s-%s" % (p["rec"], segment.stamp(p["start"]), segment.stamp(p["end"])) for p in c["parts"])
        mins = sum(p["end"] - p["start"] for p in c["parts"]) / 60
        print("  %s %-32s %-40s %5.1f min  %-6s %s" % ("✓" if c.get("approved") else " ", c["key"][:32], parts, mins,
                                                     c.get("confidence", ""), c.get("reason", "")[:60]))
        for f in c.get("flags") or []:
            print("      FLAG  " + f)
        for n in c.get("notes") or []:
            print("      note  " + n)
    for s in data.get("skipped", []):
        print("  - %-32s NOT CUT: %s" % (s["key"][:32], s.get("why", "")))
    for k in data.get("missing") or []:
        print("  ? %-32s in the lineup, neither cut nor skipped" % k[:32])
    if not recs:
        print("  (no recordings yet)")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = argv[0]
    if cmd == "doctor":
        sys.exit(1 if print_doctor() else 0)
    if cmd == "setup":
        sys.exit(1 if setup() else 0)
    if cmd == "serve":
        from . import app
        app.serve(argv[1] if len(argv) > 1 else None)
        return
    if cmd == "publish":
        from . import publish
        print(publish.publish())
        return
    if len(argv) < 2:
        raise SystemExit(__doc__)
    run = pipeline.Run(argv[1])
    try:
        dispatch(run, cmd, argv)
    except pipeline.SpaceError as ex:
        raise SystemExit("NOT ENOUGH DISK SPACE: %s" % ex)


def dispatch(run, cmd, argv):
    if cmd == "add":
        day, rest = flag(argv[2:], "--day")
        track, rest = flag(rest, "--track")
        in_place, rest = flag(rest, "--in-place", has_value=False)
        ids = run.add(rest, day=day, track=track, in_place=bool(in_place))
        print("added: %s - next: python -m live_factory analyse %s" % (", ".join(ids) or "nothing new", run.ev["id"]))
    elif cmd == "analyse":
        with run.job("analyse"):
            run.analyse(argv[2:] or None)
        print(json.dumps(pipeline.summary(run.state), indent=1))
    elif cmd == "brief":
        for p in segment.brief(run):
            print(p)
    elif cmd == "draft":
        replace, _ = flag(argv[2:], "--replace", has_value=False)
        old, new = run.cuts(), segment.draft(run)
        keep = {c["key"]: c for c in old["cuts"] if c.get("approved") or (c.get("by") != "draft" and not replace)}
        data = {"cuts": list(keep.values()) + [c for c in new["cuts"] if c["key"] not in keep],
                "skipped": [s for s in new["skipped"] if s["key"] not in keep]}
        for p in run.save_cuts(data):
            print("  note: " + p)
        list_cuts(run, segment.annotate(run, run.cuts()))
    elif cmd == "cuts":
        if len(argv) > 2:
            with open(argv[2], encoding="utf-8") as f:
                data = json.load(f)
            for p in run.save_cuts(data):
                print("  note: " + p)
        data = segment.annotate(run, run.cuts())
        for p in segment.validate(run, data):
            print("  " + p)
        list_cuts(run, data)
    elif cmd == "frame":
        from . import media
        sec = segment.parse_stamp(argv[3])
        out = os.path.join(run.rec_dir(argv[2]), "frames", "at_%d.jpg" % sec)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        print(media.still_at(run.rec_path(argv[2]), sec, out))
    elif cmd == "run":
        with run.job("run"):
            run.run()
        print(json.dumps(pipeline.summary(run.state), indent=1))
    elif cmd == "review":
        keys = list(run.state["talks"]) if "--all" in argv else argv[2:]
        for k in keys:
            t = run.state["talks"].get(k)
            if not t:
                raise SystemExit("no talk %r in this run" % k)
            if (t["steps"].get("edit") or {}).get("status") == "done":
                run.set_reviewed(k)
                print("reviewed: " + k)
            else:
                print("not edited in Descript yet: " + k)
    elif cmd == "redo":
        if argv[2] not in run.state["talks"]:
            raise SystemExit("no talk %r in this run" % argv[2])
        run.request_redo(argv[2], " ".join(argv[3:]))
        print("flagged: %s - the next run cuts it again and uploads it as a fresh composition" % argv[2])
    elif cmd == "unpause":
        why = run.state.pop("edits_paused", None)
        run.save()
        print(("edits resumed (was: %s) - press Run" % why) if why else "edits were not paused")
    elif cmd == "retry":
        t = run.state["talks"][argv[2]]
        for step, v in list(t["steps"].items()):
            if v.get("status") in ("failed", "running"):
                del t["steps"][step]
        t["error"] = None
        run.save()
        with run.job("retry"):
            run.run(only=[argv[2]])
    elif cmd == "youtube":
        run.write_youtube()
        print(open(os.path.join(run.dir, "YouTube items.txt"), encoding="utf-8").read())
    elif cmd == "status":
        print(json.dumps(pipeline.summary(run.state), indent=1))
    else:
        print(__doc__)


main(sys.argv[1:])
