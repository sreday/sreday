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
- **"Launch / activate the factory" always opens the workbench on the event being worked on**: run
  `... -m live_factory serve <event>` in the background - it opens http://localhost:8043/?event=<event> in their
  browser, reuses an app that's current and replaces one running older code. Every time, even when it's already
  running (Marek, 2026-10-17).
- **One folder per event, named after the conference** (Marek, 2026-10-17): `Desktop/video factory - LLMday Austin 2026 Q4`
  (both factories; `machine.event_dir`). Never make factory folders named by id, and never one shared folder for
  several events - several conferences can be in the factory at once.
- **Keep the factories in step with the websites**: when a change to the site repos alters something a factory reads
  (folder layout like `past/`, talks.csv columns, metadata.yml keys, teaser/thumbnail URLs, brand list), adapt the
  live factory and the Conf42 factory in the same job, and test them (Marek, 2026-10-17).
- **Keep the practices in the repo**: whenever a real event (or the person) teaches a new rule or way of working,
  write it into `live-factory/README.md` (e.g. a "Learned from a real day: <event>" section), this skill if it changes
  how you run things, and `MANUAL.md` / `MANUAL-CLAUDE.md` plus their Changelog when the team would notice - in the
  same job, not later (Marek, 2026-10-17).

## The steps

1. **Add**: `add <event> <file-or-folder ...> --day N [--track T]` (T = track number or room name; a one-room day
   needs only the day). Default copies the file in as a verified read-only backup; `--in-place` only if they ask (then
   warn that the file is the only copy).
   Sound recorded on its own device (wired set-ups): add those files for the same day too - sound extensions are
   taken as separate sound, `--sound` marks any other file; a folder gives only its videos unless `--sound`.
2. **Analyse**: `analyse <event>` (background). It ends by placing each separate sound recording on the videos
   (`sync <event> [--again]` does only that); read its notes - a recorder placed nowhere is probably another room.
3. **Match the lineup** - your main job, see below. Result: `cuts.json` via `cuts <event> <file.json>`.
   On Windows start `analyse`, `run` and `serve` with PowerShell `Start-Process` (set `$env:PYTHONUTF8` in PowerShell
   first, it is inherited; `set X=1 && ...` inside `cmd /c` leaves a trailing space and Python refuses to start) (or `cmd /c ... > log`) so they keep
   running after Claude's shell ends. If a run and the workbench both stop with no error in the log (Dallas, 03:11),
   start both again: the run carries on, and credit guard 2 skips edits Descript already finished.
   **Then the picture pass** (Marek, 2026-11-27: "the best quality sound and the best quality slides full screen -
   period. If there's a demo, then we switch to the inferior recording"; "it's easier to put in slides into the video
   than modify the colors"): `look <event> ["<talk>" ...]` (background for a full day). Per talk it finds the deck
   (`<work folder>/decks/<speaker>.pdf`, else the event's `slides/<talk page>.pdf` from the deck dump) and writes
   `cuts/<speaker>.picture.jpg` (recording left, result right). LOOK at every sheet (Read the jpg) before the run:
   a wrong page is worse than none. Talks with no deck: check the deck dump (`/deckdump`) and the Drive "Deck Dump"
   folder; download a missing deck to `<work folder>/decks/<speaker>.pdf` and run `look` again. A note "another
   version of the one presented": look at the sheet - the layout-only match is shown darkened, "NOT used". Same
   words, other fonts/line breaks (a PDF converted by Google from PowerPoint) -> set `"deck_match": "loose"` in that
   cut and `look` again (builds then show whole - say so); different words -> ask the speaker for the deck they
   presented. To keep a talk's recording as it
   is: `"picture": "video"` in its cut; to keep the camera's sound: `"sound": "camera"`.
   **Saturated sound** (Marek, 2026-11-27: pick the better track; with no choice try the tools on a sample, judge,
   repair the whole talk with a good one - which then gets the normal Studio Sound; "Descript's studio sound is the
   last resort"): `look` also says per talk whether its sound is clipped; `clipping <event>` lists only that (the
   SATURATED talks and the minutes where). Tell the person which talks are saturated before the run. The run does
   the rest by itself (`doctor` must show the repair tools ready: `local/declip-env`). After the run, read each such
   talk's cut note: which track/tool saved it, or "the last resort"; the trial's samples are in
   `cuts/<talk>.sound-trial/` - offer them for a listen. A tool that passes the judge but sounds wrong to the person:
   `"declip": "off"` for that talk and redo it.
4. **Gate 1 - the person approves the cuts** in the workbench (section 2). Never tick "approved" yourself unless the
   person explicitly says to approve (e.g. "approve all", "you're on point, ship them"); then approve only what they
   named.
5. **Run**: `run <event>` (it first makes every talk's picture plan, several at once; then each cut is CHECKED before
   its upload - deck pages in the picture, the recorder's sound in sync, the camera's pilot sound gone. A talk that
   fails stops with "stopped before Descript": read the problems and `cuts/<talk>.check.jpg`, fix (the cut, the deck,
   `"sound": "camera"`, `"picture": "video"`) and redo it - never set `"precheck": "off"` without the person's OK).
   Marek 2026-11-27: slow is fine, quality first - it runs on an idle laptop for hours; use the machine fully.
   Per talk: cut (stereo fix, black-bar zoom, 30 s before / 60 s after for hand-trimming) → Descript
   upload → AI edit (Studio Sound 80 %, um/uh only, limiter, gaps). Before the first run of an event, tell them the
   count and the cost: ~8-30 Descript AI credits per talk (Dallas 2026 Q4: 7.7-15.5, about 120 for 12 talks).
6. **Gate 2 - the person reviews each talk in Descript**, trims the extra footage, ticks Reviewed. Not you, unless
   they explicitly tell you a talk is reviewed (`review <event> "<talk>"`).
   When telling them the talks are ready, give the leftovers from `leftovers <event>` (the run also writes them to
   the log and the workbench banner): failed uploads leave compositions with the talk's own name - empty 6 s ones or
   an unedited full-length twin - and Descript's API can't delete them, so they delete the one with the length you
   name. Never count leftovers from your own memory of the night's errors (Austin: you said 3, there were 6).
7. **Run again** → publish, download, SRT, chapters, loudness, light MP4 → `<Desktop>/video factory - <event>/final videos for YouTube/`.
8. **YouTube items**: `youtube <event>` → `<Desktop>/video factory - <event>/YouTube items.txt`. Uploading to YouTube is the person's.
9. **Postmortem** (Marek, 2026-10-29): when a run ends (talks in Descript, and again after the export if more broke),
   follow the `postmortem` skill and write `live-factory/postmortems/YYYY-MM-DD-<event>.md`. Before the first step of
   the next event, read the last postmortem's open actions.

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
     Questions in the middle (during a demo) stay. "Hit me up with questions" is not a Q&A. Cut every closing Q&A:
     the 60 s margin after the cut lets the person keep it in Descript if they want (Marek, 2026-10-29: "cut Q&As,
     leave margin in descript").
   - Someone thanking the speaker by name, or "thank you for the presentation", is the MC / audience: the talk ended.
   - Names are misheard (Laya = Leila, Mailo = Mykhailo); some speakers never say their name - confirm by the topic
     (title + abstract against what is said).
   - Slots get swapped and speakers don't show; the camera may run through lunch. A talk not found → `skipped` with why.
   - Talks run 10-30 min (workshops longer).
   - A talk in the recording that is not in talks.csv (Dallas: the host's own talk) is not "not found": ask. If the
     person says add it, add the speaker to `<event>/_db/talks.csv` (and the schedule in metadata.yml if a slot has to
     move), cut it, and give them the row in the WWW sheet layout (16 tab-separated columns) for the Drive sheet.
   - Whole-day Zoom recordings have silent stretches when the room mic was muted in Zoom: `rec/<id>/levels.json`
     (one value a second) sits flat on its -90 dB floor - digital silence, not a quiet room. Nothing brings it back.
     Say so, cut what is audible, and ask for another source (a phone, a camera, the Zoom cloud recording) for whatever is lost.
   - A talk you're not sure of (low/medium confidence, picture missing, start lost) still goes to Descript when the
     person says so ("you can put them in descript, I will check later", Marek 2026-10-29); name those talks again
     when they are ready to review.
   - **One recording can switch rooms** (PLATFORMday SF 2026-11-10: the keynotes, then the mini room). Before trusting
     `draft`, make a contact sheet of `rec/<id>/frames` and read the Meet/Zoom tile name ("All Hands", "Boardroom").
     Cut each talk from whichever recording shows its room; the `--track` from `add` only steers the brief.
   - **Files that are already one edited talk each** (a partner's Premiere export, LLMday TLV 2026-11-24): match by
     file name and self-introduction, not `draft`, and send each whole (start 0, end = ffprobe duration, Q&A kept)
     unless the person asks for trims ("don't trim too hard - it was already edited"). Leave out background loops
     and Premiere cache.
   - **An emptied room keeps recording bleed from the other room** (LLMday SF 2026-11-24: the meeting room moved to
     the main room). Whisper lines that echo the other recording a few seconds apart are bleed, not a talk. Zoom's
     separate `.m4a` in the Drive zip has the same Zoom mute as the video - it can't fill a muted stretch.
   - `draft` refuses to save when two of its guesses overlap. That is fine: it is only a first guess, and you write
     cuts.json yourself from the brief.
3. Write cuts.json (`{"cuts":[{"key","parts":[{"rec","start","end"}],"confidence","reason","by":"claude"}],"skipped":[...]}`),
   load it with `cuts <event> file.json` (validates + flags), and give the person a table of every cut with anything
   flagged or uncertain called out. Ask them about talks you're unsure of (e.g. who spoke last).

## Hard rules

- **Raw recordings are never written, moved or deleted** - not the originals, not the copies in `in/` (the code
  refuses anyway). Don't delete anything in the work folder without asking.
- Never put a Descript token, password or the person's private data in a file, a commit or the chat.
- "EDITS PAUSED": read the edit reports in state.json (`talks.<name>.steps.edit.report`) and check in Descript before
  `unpause` - the guard exists because the AI editor once could touch other talks.
- Disk: the factory refuses jobs that don't fit and says how much to free - relay that, and never delete things to
  make room on your own.
- **Low disk space: offer a junk and cache clean-up** (Marek, 2026-10-17). When the factory refuses a job for space, the
  health check says "free some space", or the person mentions a full disk, offer to clean junk and cache files from
  their drive. Never delete on your own:
  1. Measure first (`du -sh` on the Mac, folder sizes on Windows) and show a table: what it is, its size, and whether it
     comes back by itself.
  2. Candidates, all safe to lose:
     - Mac: the Trash; `~/Library/Caches` (quit the apps first); Xcode `DerivedData` and unavailable simulators
       (`xcrun simctl delete unavailable`); `brew cleanup`; `npm cache clean --force`; `pip cache purge`; unused Docker
       data (`docker system prune`); video apps' caches (Descript's local media cache, Adobe Media Cache, DaVinci
       Resolve CacheClip, Final Cut render files).
     - Windows: the Recycle Bin, `%TEMP%`, Disk Cleanup (`cleanmgr`, including Windows Update clean-up), browser caches,
       the same app and developer caches.
     - On either: old installers in Downloads (`.dmg`, `.pkg`, `.exe`, `.msi`, `.zip`) - list them and let the person
       pick.
  3. Delete only what the person picks, say how much was freed, run `doctor` again and carry on with the job.
  - **Never offer:**
    - recordings, event folders, `in/` copies, or anything on the Desktop the person put there;
    - Documents, Photos and Lightroom libraries, iCloud or OneDrive files;
    - git repos and their worktrees - point those out, but the person (or that repo's own session) decides;
    - running VMs.
- One computer per event.
- After the person's runs, `publish` keeps the website's Factory logs tab current (runs also publish by themselves).
- If you change the factory's code or behaviour: update `live-factory/MANUAL.md` (and `MANUAL-CLAUDE.md`) plus the
  Changelog in the same commit, run `env/bin/python tests/smoke.py --no-whisper` and `env/bin/python tests/av_smoke.py`,
  and commit only your files.
