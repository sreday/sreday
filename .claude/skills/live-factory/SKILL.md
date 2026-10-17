---
name: live-factory
description: Operate the LIVE talk factory (live-factory/ in this repo) - turn the long recordings of a live SREday / LLMday / PLATFORMday / PEC / DevOps Not Dead event into one cleaned video per talk via Descript. Use when someone asks to add or analyse recordings, match the lineup / find the talks, cut talks, run Descript, check the factory status, redo a talk, export, write YouTube items, or set the factory up on a computer.
---

# LIVE talk factory - how Claude runs it

The team manual (what the person sees) is `live-factory/MANUAL.md` and `live-factory/MANUAL-CLAUDE.md`, published at
sreday.com/factory (password: ask the person). Technical details: `live-factory/README.md`. Read the README section
you need before changing anything.

## Basics

- Run everything from `live-factory/` with its venv and UTF-8:
  - Mac/Linux: `cd live-factory && PYTHONUTF8=1 env/bin/python -m live_factory <command> ...`
  - Windows: `cd live-factory; $env:PYTHONUTF8=1; env\Scripts\python -m live_factory <command> ...`
- No `env/` yet: the person runs `bash live-factory/setup.sh` (Mac) or `live-factory\setup.cmd` (Windows) themselves -
  it asks for their Descript API token, which never goes through you. Then `... -m live_factory doctor`.
- Events are `<brand>-<folder>`: `sreday-2026-london-q1`, `llmday-2026-london-q4`, `platformday-2026-austin-q4`.
  `git pull` the brand repos first so the lineup is current (the lineup is the event's `_db/talks.csv`; never edit it -
  it is a Drive export).
- Long jobs (`analyse`, `run`) go in the background; tell the person the workbench (http://localhost:8043) and the
  status page (http://localhost:8043/status) show progress. An 8 h day analyses in ~35 min on an M1/M2 Mac.
- Start the app for them: `./run.sh` (Mac) / `run.cmd` (Windows).

## The steps

1. **Add**: `add <event> <file-or-folder ...> --day N [--track T]` (T = track number or room name; a one-room day
   needs only the day). Default copies the file in as a verified read-only backup; `--in-place` only if they ask (then
   warn that the file is the only copy).
2. **Analyse**: `analyse <event>` (background).
3. **Match the lineup** - your main job, see below. Result: `cuts.json` via `cuts <event> <file.json>`.
4. **Gate 1 - the person approves the cuts** in the workbench (section 2). Never tick "approved" yourself unless the
   person explicitly says to approve (e.g. "approve all", "you're on point, ship them"); then approve only what they
   named.
5. **Run**: `run <event>` - cut (stereo fix, black-bar zoom, 30 s before / 60 s after for hand-trimming) → Descript
   upload → AI edit (Studio Sound 80 %, um/uh only, limiter, gaps). Before the first run of an event, tell them the
   count and the cost: ~8-30 Descript AI credits per talk.
6. **Gate 2 - the person reviews each talk in Descript**, trims the extra footage, ticks Reviewed. Not you, unless
   they explicitly tell you a talk is reviewed (`review <event> "<talk>"`).
7. **Run again** → publish, download, SRT, chapters, loudness, light MP4 → `<Desktop>/live-factory/final videos for YouTube/`.
8. **YouTube items**: `youtube <event>` → `<work folder>/<event>/YouTube items.txt`. Uploading to YouTube is the person's.

Other commands: `status <event>`, `retry <event> "<talk>"`, `redo <event> "<talk>" "why"` (cut again + fresh
Descript composition "<talk> (redo N)" on the next run), `unpause <event>`, `publish` (Factory logs tab of the website),
`frame <event> r1 h:mm:ss` (one still - look at it with Read), `draft`, `brief`.

## Matching the lineup (step 3) - be careful, a wrong cut is worse than none

1. `draft <event>` for the automatic first guess, then `brief <event>` and **read the brief** (lineup with abstracts,
   self-introductions, MC intros, closing lines, pauses, the transcript).
2. For every talk, read the transcript around its start and end yourself (`rec/<id>/transcript.json` in the work
   folder) and look at frames when unsure. Rules learned on real days:
   - A talk starts at the speaker's first words to the room - often a greeting *before* the name ("Hey, thanks for
     being here today ... So I'm Jay") - never on a mic check ("can you hear me", "test") or set-up chat.
   - It ends at their closing ("that's my time / that's it from me / thank you"). Cut the closing Q&A when it clearly
     is one ("any questions?", "let's do Q&A", "I'm gonna take questions now", then someone asking from the back).
     Questions in the middle (during a demo) stay. In doubt, keep the Q&A. "Hit me up with questions" is not a Q&A.
   - Someone thanking the speaker by name, or "thank you for the presentation", is the MC / audience: the talk ended.
   - Names are misheard (Laya = Leila, Mailo = Mykhailo); some speakers never say their name - confirm by the topic
     (title + abstract against what is said).
   - Slots get swapped and speakers don't show; the camera may run through lunch. A talk not found → `skipped` with why.
   - Talks run 10-30 min (workshops longer).
3. Write cuts.json (`{"cuts":[{"key","parts":[{"rec","start","end"}],"confidence","reason","by":"claude"}],"skipped":[...]}`),
   load it with `cuts <event> file.json` (validates + flags), and give the person a table of every cut with anything
   flagged or uncertain called out. Ask them about talks you're unsure of (e.g. who spoke last).

## Hard rules

- **Raw recordings are never written, moved or deleted** - not the originals, not the copies in `in/` (the code
  refuses anyway). Don't delete anything in the work folder without asking.
- Never put a Descript token, password or the person's private data in a file, a commit or the chat.
- "EDITS PAUSED": read the edit reports in state.json (`talks.<name>.steps.edit.report`) and check in Descript before
  `unpause` - the guard exists because the AI editor once could touch other talks.
- Disk: the factory refuses jobs that don't fit and says how much to free - relay that, don't delete things to make room.
- One computer per event.
- After the person's runs, `publish` keeps the website's Factory logs tab current (runs also publish by themselves).
- If you change the factory's code or behaviour: update `live-factory/MANUAL.md` (and `MANUAL-CLAUDE.md`) plus the
  Changelog in the same commit, run `env/bin/python tests/smoke.py --no-whisper`, and commit only your files.
