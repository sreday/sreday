---
name: live-factory
description: Operate the LIVE talk factory (live-factory/ in this repo) - and set it up on a new computer by itself (bootstrap.py + the sign-in vault on Drive) - turn the long recordings of a live SREday / LLMday / PLATFORMday / PEC / DevOps Not Dead event into one cleaned video per talk via Descript. Use when someone asks to add or analyse recordings, match the lineup / find the talks, cut talks, run Descript, check the factory status, redo a talk, export, write YouTube items, or set the factory up on a computer.
---

# LIVE talk factory - how Claude runs it

The team manual (what the person sees) is `live-factory/MANUAL.md` and `live-factory/MANUAL-CLAUDE.md`, published at
sreday.com/factory (password: ask the person). Technical details: `live-factory/README.md`. Read the README section
you need before changing anything.

## Basics

- Run everything from `live-factory/` with its venv and UTF-8:
  - Mac/Linux: `cd live-factory && PYTHONUTF8=1 env/bin/python -m live_factory <command> ...`
  - Windows: `cd live-factory; $env:PYTHONUTF8=1; env\Scripts\python -m live_factory <command> ...`
- **First thing on any factory request: is this computer set up?** No `live-factory/env/`, or `doctor` shows a
  missing Descript token / YouTube sign-in: do "A new computer" (below) yourself, right away, without asking - the
  bootstrap and the sign-ins from Marek's Drive vault - then carry on with what he asked. Never ask him for a token or
  send him to setup.sh (that stays only for someone who sets up by hand, without Claude).
- Events are `<brand>-<folder>`: `sreday-2026-london-q1`, `llmday-2026-london-q4`, `platformday-2026-austin-q4`.
  `git pull` the brand repos first so the lineup is current (the lineup is the event's `_db/talks.csv`; never edit it -
  it is a Drive export).
- Long jobs (`analyse`, `run`) go in the background; tell the person the progress page (http://localhost:8043:
  plain progress bars, then only the real issues - Marek 2026-11-27) shows how far it is. The detailed workbench is
  http://localhost:8043/workbench (cuts, recordings, review, redo), the status page http://localhost:8043/status. An 8 h day analyses in ~35 min on an M1/M2 Mac.
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

## A new computer - "set up the factory on this computer"

Marek, 2026-11-28: "I really need this flow: copy repo, install plugins, run this puppy - as long as my claude is
used"; "I want the youtube sign in to be automatic when signed with my claude"; "same for descript, no individual api
keys". So on a computer with nothing, Claude does it all, without asking him for any token. The full, plain recipe
(missing Git / Python, the Drive connector off, the hand-over) is `live-factory/SETUP.md` - follow it; in short:

1. The repos side by side (`git clone` what is missing: sreday, llmday, platformday,
   Prompt-Engineering-Conference/2026.promptengineering.rocks - not devopsnotdead), `git pull` the rest.
2. Python 3.9+ and Git must be there (Mac: `brew install python`; Windows: `winget install -e --id
   Python.Python.3.12`). Then `python3 live-factory/bootstrap.py` (Windows: `py -3 live-factory\bootstrap.py`). With
   no questions it does the env, the packages, the CUDA libraries on an NVIDIA PC, ffmpeg, settings.local.yml and the
   health check.
3. The sign-ins from the vault: in his Drive folder "Claude (private)" (owner-only; search `title = 'live-factory
   vault.json'`) with the Google Drive connector, `download_file_content` it, Write the base64 it returns as it is
   into `~/.live-factory/vault.b64`, then `env/bin/python -m live_factory vault install ~/.live-factory/vault.b64`
   (Windows: `env\Scripts\python`). That deletes the file. The tokens pass through your context here: never echo,
   quote or summarise them, and never write them anywhere else.
4. `doctor`: everything OK, "YouTube sign-ins" lists the brands, the Descript token works. Done - tell him "ready".

Keep the vault current: after any `youtube-login` or a new Descript token, `vault pack ~/.live-factory/vault.json`,
then `update_file` the Drive vault (or create it in "Claude (private)" if missing) with
base64Content = the file reference `sha256:<hex>:<path with />` - the content never passes through you - then delete
the local pack. The OAuth client file (`youtube-client.json`) is only needed for a NEW channel sign-in; the vault's
tokens refresh by themselves.

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
   **Sharp ends** (Marek, 2026-11-27: "the beginnings and the ends of talks super sharp and production-ready ... Q&A
   need to always be outed ... end sharply at the moment when the person finishes, but letting him finish the
   sentence" - "full auto, so sharp cuts with guardrails"): after cuts.json, run `ends <event>` (background; ~1-2 min
   a talk on the GPU). It listens closely (Whisper, word timestamps) at every talk's edges and writes
   `ends_brief.md` in the work folder. **Read the whole brief** - your job, as for the lineup:
   - Start: the speaker's first words to the room. Not the MC, not "can you hear me?", "are we good to go?",
     "Okay, thank you. I think we can start", "let me adjust this". A greeting, the name, "thanks for having me", or a
     cold open after a pause ("So my talk is about ...", "Who here ...?") are fine.
   - End: the speaker's last finished sentence before the Q&A - ideally the closing "thank you". Always without the
     Q&A, the MC ("Thank you very much, Greg"), the room ("thank you for the presentation", a question), "any
     questions?" / "happy to take questions" / "so yeah". Never mid-sentence or on "and" / "so"; when the last
     sentence can't stand as an end, the one before it. When the close trails off (mumbling, ums, a run with no
     punctuation) after a firm sentence, end on the firm sentence (Marek: "always end on the firm sentence, if it's
     followed by some mumbling and stuff").
   - For each talk, confirm or move the edges in cuts.json:
     `"ends": {"start": "0:31:12.4", "end": "0:52:40.1", "by": "claude", "why": "...", "first": "Good morning everyone,
     I'm ...", "last": "... that's it, thank you."}` (recording time). Write "first"/"last" (a few of the words the
     edge falls on) whenever you move an edge: the check after the cut hears the cut against them, and without them
     the moved edge goes unchecked (London Q3 2026: 12 talks stopped before Descript, checked against the sentence
     at the old edge). A FLAGGED
     talk stops before cutting until you do. `"ends": "rules"` takes the rules' edges as they are; `"ends": "off"`
     cuts the coarse parts, with no sharp ends.
   - When the start is lost (the sound only comes back mid-sentence), say so: start on the first full sentence.
   Then give the person each talk's first and last sentence at gate 1.
4. **Gate 1 - the person approves the cuts** in the workbench (section 2). Never tick "approved" yourself unless the
   person explicitly says to approve (e.g. "approve all", "you're on point, ship them"); then approve only what they
   named.
5. **Run**: `run <event>` (it first makes every talk's picture plan, several at once; then each cut is CHECKED before
   its upload - deck pages in the picture, the recorder's sound in sync, the camera's pilot sound gone. A talk that
   fails stops with "stopped before Descript": read the problems and `cuts/<talk>.check.jpg`, fix (the cut, the deck,
   `"sound": "camera"`, `"picture": "video"`) and redo it - never set `"precheck": "off"` without the person's OK).
   A talk the selfcheck calls PROBLEMATIC because "Descript took out more than gaps and fillers" (its transcript
   dropped real speech and the gap/filler edit cut it: London Q3 2026, Michael Cade, 53% gone): check the whole-day
   transcript has speech there, then `"edit": "sound only"` in its cuts.json entry + `redo <event> "<talk>"` - the
   new composition gets the sound settings only, nothing cut.
   Marek 2026-11-27: slow is fine, quality first - it runs on an idle laptop for hours; use the machine fully.
   Per talk: cut (stereo fix, black-bar zoom, the sharp ends - no extra footage; the cut's own first and last words
   heard again before the upload) → Descript
   upload → AI edit (Studio Sound 80 %, um/uh only, limiter, gaps). Before the first run of an event, tell them the
   count and the cost: ~8-30 Descript AI credits per talk (Dallas 2026 Q4: 7.7-15.5, about 120 for 12 talks).
6. **Gate 2 - the person reviews each talk in Descript** (the edges are final already - no trimming), ticks Reviewed. Not you, unless
   they explicitly tell you a talk is reviewed (`review <event> "<talk>"`).
   When telling them the talks are ready, give the leftovers from `leftovers <event>` (the run also writes them to
   the log and the workbench banner): failed uploads leave compositions with the talk's own name - empty 6 s ones or
   an unedited full-length twin - and Descript's API can't delete them, so they delete the one with the length you
   name. Never count leftovers from your own memory of the night's errors (Austin: you said 3, there were 6).
7. **Run again** → publish, download, SRT, chapters, loudness, light MP4 → `<Desktop>/video factory - <event>/final videos for YouTube/`.
8. **YouTube**: `youtube <event>` → `<Desktop>/video factory - <event>/YouTube items.txt`. Without autopilot the
   upload is the person's; on autopilot (below) the factory uploads (`upload <event>`).
9. **Postmortem** (Marek, 2026-10-29): when a run ends (talks in Descript, and again after the export if more broke),
   follow the `postmortem` skill and write `live-factory/postmortems/YYYY-MM-DD-<event>.md`. Before the first step of
   the next event, read the last postmortem's open actions.

Other commands: `status <event>`, `retry <event> "<talk>"`, `redo <event> "<talk>" "why"` (cut again + fresh
Descript composition "<talk> (redo N)" on the next run), `unpause <event>`, `publish` (Factory logs tab of the website),
`frame <event> r1 h:mm:ss` (one still - look at it with Read), `draft`, `brief`.

## Ship an event (autopilot) - "please ship talks from <event> through live talk factory"

Marek, 2026-11-27: "you use these polished processes to ensure maximum quality, run them locally through our tooling,
run through descript, download and push into respective sre/llm/platform/pec youtube channels. Then instead of asking
for permission to run this further, you'll ship it and write a post-mortem ... you'll ask for forgiveness not for
permission. If we want to go fast, this is the only way". So on such a request nothing waits for him.

**No questions mid-run** (Marek, 2026-11-28: "the factory doesn't ask any questions mid-run. Just ship it, ask for
forgiveness not permission"). From his request to the summary at the end you never ask him anything and never stop
to wait - not for a cost, an unsure talk, a deck, an extra talk or a missing source. Decide with the rules here and
write each call into the report, so he can say "fetch X" / "redo Y" afterwards:
- an unsure lineup match: cut it when the evidence points one way (name, topic, slot) and say so in the report; when
  it doesn't, skip it as "not sure which talk this is" - never wait for an answer;
- a talk in the recording that is not in talks.csv: not cut; named in the report with its time, to add if he wants;
- a lost stretch (muted mic, a camera off): cut what is audible, or skip; the report asks for another source;
- a deck that is another version: the recording's own picture (`"picture": "video"`), noted in the report;
- unsure sharp ends: confirm or move them yourself; if you can't make sure, the talk is held and named in the report;
- the Descript cost: a line in the report.
The only things that stop a run are the hard stops below. Even then: finish everything that can be finished, then
report what is blocked in one plain sentence each - don't ask in the middle.

**Survival of the fittest** (Marek, 2026-11-28: "all good talks get shipped without inference, the bad /
unshippable ones are stopped and questions are asked. If unsure whether it's shippable but it's very likely, give
benefit of the doubt and ship to have me check the final video"). Three buckets:
- **good** - shipped, nothing said;
- **very likely fine** - shipped on the benefit of the doubt, and named under "Please check" (the video is private
  until its release, weeks later, so he has time). For an unsure match or unsure ends you decide in: approve it by
  name (`approve <event> "<talk>"` - a named talk is not held) and put the why in its cut: `"check": "very likely
  Bo's talk, but the name was not heard"`. The factory's own check does the same for its doubts: one pause of 8-30 s,
  10-30 s of black, a final 60-75% of the cut, first/last words heard differently, the loudness;
- **unshippable** - not shipped: not recorded, the sound lost (no sound, silences over 10% or one of 30 s+), the start
  lost, black for 30 s+, a final under 60% of the cut. They go into the one question at launch.

**Unshippable talks wait; the questions come last; the night is one job** (Marek, 2026-11-28: "let's not ship the
talks that are unshippable, and ask if we have these materials AFTER all others are done. Basically, I want to be
able to put the laptop down for the night and make sure it's not going to stop mid run"):
- A talk with no usable material (not recorded, sound lost, start lost) is NOT shipped: skipped or held, never
  shipped half-broken.
- Do every judgment call FIRST, while he is around: steps 1-6 below (the lineup, the ends, the decks, approve).
- Then start ONE background job, `ship <event>` (Windows: PowerShell Start-Process, as for `run`). It runs the whole
  night with nothing waiting for anyone:
  - the run;
  - talks that failed for a passing reason, again 30 min later, twice;
  - YouTube's day full: it waits for the next quota day and goes on, up to 3 days;
  - the ship report, ending with "To ask for now".

  Before saying "put the laptop down", look at `doctor`'s "Power" line (machine.battery_line): on battery, say so
  plainly in the launch message - "this laptop is on its battery - plug it in before you leave it". The job notes it
  too, and the progress page shows a yellow banner while it lasts (Marek 2026-11-28).
  Marek, 2026-11-28: "it's ok to ask straight away when launching, but I don't want it to stop later on". So in the
  SAME message that says the job is running, ask the materials list in one go: "Do you have the recording of X /
  another recording with sound of Y / the slides Z presented?". It is the only question of the whole ship, and it
  never blocks: the job is already running and ships everything else. Then: "You can put the laptop down (plugged
  in, lid open) - I'll have the report in the morning."
- Whatever he sends later goes in as a follow-up (`add`, match, then `ship` again, which only does the new talks);
  nothing waits for it.
- When the job ends: the sheet links (step 9), a look at the site push lines (step 10), the postmortem, and in the summary what
  is still missing - as a statement, not a new question.

1. Preflight - the launch checklist of MANUAL-CLAUDE.md ("Launch checklist"):
    - `git pull` the brand repo, and read the last postmortem's open actions;
    - run `doctor`: everything OK; the packages must not be missing (run bootstrap.py again after a requirements.txt
      change); "YouTube sign-ins" must list the brand - if not, install them from the vault ("A new computer"); if the
      vault has no sign-in for it either, ship everything else and say so in the report (never ask mid-run);
    - check the event's `metadata.yml`: `start_time` with its offset, `city_name` in settings `youtube.timezones`
      (add a missing city in a lane), and `youtube_url`;
    - open one talk's thumbnail at `/<event>/teasers/<speaker>.png`;
    - check the free disk space;
    - run from the computer's own `sreday/live-factory`, never from a worktree: the ledger of paid edits lives in its
      `local/`.
2. `add` the materials he gives, `analyse`, match the lineup (as below, just as carefully), `cuts <event> file.json`.
3. `autopilot <event> on`.
4. `ends <event>`: read the whole brief and confirm or move EVERY flagged talk yourself in cuts.json (`"ends"` with
   `"by": "claude"`). A talk you can't make sure (the start lost, the sound dies) stays flagged: it is left out and named
   in the report - never guessed.
5. `look <event>`: read every sheet; fix decks, `"picture": "video"`, `"sound": "camera"` yourself.
6. `approve <event>` (gate 1 by you; it holds talks whose ends are still flagged). Put the Descript cost in the report,
   not in a question.
7. `ship <event>` (background - the night job above; `run <event>` does the same without the later rounds and the
   wait for YouTube's next day). On autopilot there is no review in Descript: after the export the factory checks each
   final itself (`selfcheck`: first and last words heard again, silences, black picture, loudness, length). A
   PROBLEMATIC talk does not go to YouTube. The checked talks go to the brand's channel: private, public 5 weeks after
   the event at 09:00 local, thumbnail from the website, captions, the event's playlist in page order. A talk that
   fails a step is retried by the run; a stopped one: fix it (redo, the cut, the deck) and run again - your call.
8. `upload <event>` again the next day when the day's YouTube quota stopped it (the run says so). The scheduled
   catch-up does this by itself (`catchup`, at login + every 3 h; SETUP.md section 5): check `local/catchup.log`
   that it is installed and ran, rather than asking the person to keep a window open.
9. The WWW sheet: each uploaded talk's `https://youtu.be/<id>` into the YouTube column of the event's tab (Google
   Sheets connector), the row found by the speaker's name right before writing (rows move), only empty cells, never
   over a typed link. The website already has them (step 10); the sheet keeps them for the next lineup ship.
10. The playlist: the upload uses the one the event's website links (`youtube_playlist` in its entry of the brand's
    `home/metadata.yml`), else finds the channel's playlist for the event (brand, city, year and quarter in any order:
    the team named some by hand, "SREday London Q1 2026"), else makes one. The videos are added in the website's order
    (the schedule, track by track).
    The website gets everything straight away, by itself (Marek 2026-11-28: "every time a playlist or a video is
    added, push straight"): after each upload the run pushes the talk's link (talks.csv), the playlist
    (`youtube_playlist` in the event's metadata.yml and its home card) and `video_release` (the exact publishAt) to
    the brand repo's main - `site: pushed ...` in the log. Until the release the pages show "Video ready - premieres
    in 14d 3h" with a countdown and the next event to attend; the player appears by itself at the release. Only a
    `site: not pushed ...` line needs you: fix the cause (GitHub sign-in, internet); the next pass pushes it. "HOOK
    THE PLAYLIST TO THE EVENT" now shows only when that push failed - then do it by hand in a lane.
11. `report <event>` -> `ship report.md`, then the postmortem (`postmortem` skill): on YouTube + when public, missing
    and why, problematic and what would fix it, credits, Descript leftovers to delete by hand. Give him the summary.

The captions are the Descript transcript proofread in the talk's context (step `proofread`, right after the
download): glossary.yml, then Claude through the Claude Code CLI on this computer (proofread_model: opus) - only
misheard words change, never timings; the Descript original stays in srt/descript/, the fixes in srt/proofread/. A
partial proofread (Claude not reachable) is tried again before the upload. `doctor` must show "Claude Code
(transcript proofread)" OK.

What goes up is in the channels' own style (read from them 2026-11-27):
- the title is "<title> | <speakers> | SREday Dallas 2026 Q4", within 100 characters, with Descript's short title
  when the real one doesn't fit, and "Keynote: " in front of a keynote's;
- the description has the event line, the ticket and CFP links, and the chapters;
- the tags are the channel's own set plus the event and the city;
- the thumbnail is the website's YouTube picture (`/<event>/teasers/<speaker>.png`, 1280x720).

DevOps Not Dead is dead (Marek, 2026-11-27: "don't touch devops not dead it's a dead thing"): no work there, no
sign-in.

PEC is on the shelf (Marek 2026-11-28: "put PEC to the wardrobe for now, it's a friend's channel and I don't have
full permissions"): settings `youtube.no_upload` - a PEC event is shipped up to the finished videos in the final
folder, no upload, no sign-in asked for; the report says where the videos are. Don't raise PEC's channel again
unless he does.

Hard stops even on autopilot: no Descript token, no YouTube sign-in for the brand, the disk, EDITS PAUSED, a channel
that isn't the brand's. Follow-ups: "fetch talk X" = `add` the new file, match, the same path; "redo Y" = `redo`, then
`run` - a never-public old video is deleted and replaced, a public one stays (say so) and the new one goes up.

## Matching the lineup (step 3) - be careful, a wrong cut is worse than none

1. `draft <event>` for the automatic first guess, then `brief <event>` and **read the brief** (lineup with abstracts,
   self-introductions, MC intros, closing lines, pauses, the transcript).
2. For every talk, read the transcript around its start and end yourself (`rec/<id>/transcript.json` in the work
   folder) and look at frames when unsure. Rules learned on real days:
   - A talk starts at the speaker's first words to the room - often a greeting *before* the name ("Hey, thanks for
     being here today ... So I'm Jay") - never on a mic check ("can you hear me", "test") or set-up chat.
   - It ends at their closing ("that's my time / that's it from me / thank you"). Cut the closing Q&A when it clearly
     is one ("any questions?", "let's do Q&A", "I'm gonna take questions now", then someone asking from the back).
     Questions in the middle (during a demo) stay. "Hit me up with questions" is not a Q&A. Cut every closing Q&A
     (Marek, 2026-11-27: "Q&A need to always be outed as they don't prove consistency"); `ends` then sets the exact
     words.
   - Someone thanking the speaker by name, or "thank you for the presentation", is the MC / audience: the talk ended.
   - Names are misheard (Laya = Leila, Mailo = Mykhailo); some speakers never say their name - confirm by the topic
     (title + abstract against what is said).
   - Slots get swapped and speakers don't show; the camera may run through lunch. A talk not found → `skipped` with why.
   - Talks run 10-30 min (workshops longer).
   - A talk in the recording that is not in talks.csv (Dallas: the host's own talk) is not "not found": ask (on
     autopilot: don't ask - leave it uncut and name it in the report, with its time). If the
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
   flagged or uncertain called out. Ask them about talks you're unsure of (e.g. who spoke last) - in the step-by-step
   flow only; on autopilot decide by the rules in "No questions mid-run" and put it in the report.

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
