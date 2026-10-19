# LIVE talk factory

> **Team manual:** `MANUAL.md` next to this file is the step-by-step guide for the team, published unlisted at sreday.com/factory (noindex, not in the sitemap). **Whenever the factory changes in a way the team would notice (a step, a button, a setting, a fix), update MANUAL.md and its Changelog in the same commit.**

Long live recordings in, one clean YouTube-ready video per talk out, for any brand (SREday, LLMday, PLATFORMday,
PEC, DevOps Not Dead). Descript gets only the talks, never the breaks, MC chatter and silence, so its transcription
credits and media hours go to the talks alone. This is the live-event sibling of the Conf42 factory
(`conf42/factory/`), which handles pre-recorded talks.

```
SD card / disk ──add──> analyse (local, free) ──> cuts.json ──GATE 1──> cut + stereo fix ──> Descript edit
                         levels, Whisper,           Claude matches        (you approve)                │
                         stills, proxy              the lineup                                         ▼
          YouTube items.txt <── loudness + trim <── srt, chapters <── publish <──GATE 2── you review in Descript
```

## Setup (once per machine)

- Mac: `bash live-factory/setup.sh`
- Windows: double-click `live-factory\setup.cmd` (on an NVIDIA PC it also installs Whisper's CUDA libraries)

This creates `live-factory/env` (git-ignored) and installs faster-whisper. The Descript token is reused from the
Conf42 factory if that machine has it. Then: `python -m live_factory doctor`.

## A day of recordings

Events are named `<brand>-<folder>`, e.g. `sreday-2026-london-q1` or `llmday-2026-london-q4`. Run the commands
from `live-factory/` with `env/bin/python -m live_factory …` (Windows: `env\Scripts\python.exe -m live_factory …`),
or use the app: `./run.sh` / `run.cmd` → http://localhost:8043 (`serve <event>` opens it on that event).

The app opens on `?event=` or else the event whose `state.json` changed last; a remembered pick only wins while that
event has been started. Events in a repo's `past/` are listed. An app still running from older code is replaced:
`serve` compares `/api/version` (the code's newest mtime when it started) with the code now and asks the old one to
quit (`/api/quit`); jobs run in their own processes and carry on. (2026-10-17: an app left running from the day
before didn't know about `past/` and opened on DevOps Not Dead London instead of LLMday Austin.)

1. **Add.** `add sreday-2026-london-q1 /Volumes/CARD/DCIM --day 1 --track 2` copies the files in. `--track` is
   the schedule track or the room name ("screen 2"); a day with one track needs only `--day`. Use `--in-place`
   to read the files where they are. The source files are never changed.
2. **Analyse.** `analyse sreday-2026-london-q1` runs once per recording and is resumable. It writes:
   - per-second left/right levels;
   - a Whisper transcript (small on the Mac CPU, about 10x real time; large-v3 on an NVIDIA card);
   - stills;
   - a 480p proxy, only when the browser can't play the file itself.
3. **Match the lineup.** `draft` makes a first guess: it uses the MC's "please welcome …", pauses checked
   against the real audio levels, and the running order with its delays carried forward. `brief` writes
   `segment_brief_<track>.md` for Claude, who reads it, looks at frames
   (`frame <event> r1 1:02:30`) and writes `cuts.json`. Ask Claude: "match the lineup for
   sreday-2026-london-q1".
4. **Gate 1, in the app.**
   - Each track shows the recording with a waveform: left channel above the line, right below, so a one-sided
     recording shows half a wave.
   - Each cut has ±1/±5 s nudges, ▶ start / ▶ end, and the transcript around the boundaries.
   - Approve the good cuts and save. Nothing goes to Descript before this.
5. **Run.** Every approved cut is cut out with ffmpeg; a one-sided talk gets its live channel copied to both
   sides. It is then uploaded to the event's Descript project and edited with Studio Sound 80%, a limiter, filler
   words removed, and gaps over 2 s shortened to 1 s (`edit_prompt` in settings.yml). The run then stops.
6. **Gate 2.** Watch each talk in Descript and tick Reviewed in the app (or
   `review <event> "<speaker>"`). Then Run again: publish, download, SRT, chapters, loudness at -14 LUFS, trim to
   the speech, and QA.
7. **YouTube.** `Desktop/video factory - <event>/YouTube items.txt` holds, per talk:
   - the title (`<title> | <speakers> | SREday London 2026 Q1`, at most 100 characters);
   - the description (talk page, event, upcoming events, CFP, chapters);
   - the link to the thumbnail the website already draws (`/<event>/teasers/<speaker>.png`).

   The finished videos are in the event folder's `final videos for YouTube/`.

## Rules it keeps

- **The raw recordings are never touched.** Originals are only ever read. Each copy in `in/` is checked against
  its original (size plus a sampled hash), then made read-only and kept for good as the backup. Every write and
  delete in the code goes through a guard that refuses a raw file. `--in-place` makes no copy, and the app marks
  those files "the only copy!".
- **A wrong cut is worse than none.** A talk starts at the speaker's own "Hey, I'm Dan", or failing that, right
  after the MC's introduction. It ends after their Q&A ("Thank you, any questions?" and the last answer), and
  before anyone thanks them by name (that's the MC). Every cut is checked against what is said in it:
  - the words in it, compared with every title and abstract (a cut that sounds like another talk is flagged as a
    swapped slot);
  - quiet or distorting sound, drop-outs, too few words, an unsure transcript, a blank picture;
  - a length far from its slot.

  Flagged and low-confidence cuts are never approved in bulk. Talks in the lineup that aren't found are listed as
  "not recorded properly, or a no-show?".
- **Disk space.** Before adding, analysing or running, the factory works out what the rest of the job needs (copies,
  review copies, cuts, Descript's downloads, finished videos), keeping `disk_reserve_gb` spare. If that doesn't
  fit, nothing starts: a desktop notification goes out and the app shows a red banner. A copy that fills the disk
  midway is removed; the original is untouched.
- **Lightweight MP4s.** The cut that goes to Descript stays close to the source (`cut_crf: 18`), because Descript
  encodes it again. The finished video is H.264 at constant quality (`final_crf: 21`, `final_preset: medium`) with
  160k AAC, usually a fraction of Descript's export at the same look. If it ever comes out bigger, the export's
  picture is kept.

## Trimming, zoom and redo (Marek, 2026-10-16)

- **Extra footage for trimming by hand.** The automatic start and end are right on about 80% of talks, and on the
  rest a fixed rule cuts too much. So every clip sent to Descript gets `pad_before_seconds: 30` before the judged
  start and `pad_after_seconds: 60` after the judged end, never reaching into the next talk's own cut. The workbench
  shows where the talk sits in the clip ("talk runs 0:30 → 25:12"); trim the rest in Descript before ticking
  Reviewed.
- **Black bars.** When the slides sit in a steady box smaller than the frame, they are zoomed to *fit*: they grow
  until they touch the frame's edges, so nothing is cut off whatever their shape. On PLATFORMday Austin, Thomas
  Cameron's 1600×810 slides became a 1.2× zoom. A talk where the picture changes (camera and slides, or dark slides
  that fool the detection) is left as it is.
- **Flag for redo.** In the workbench's Descript table, say what's wrong and press "flag for redo" (or run
  `redo <event> "<talk>" "why"`). Fix the talk's cut in gate 1 first if needed. The next Run cuts it again with the
  cut, extra footage and zoom as they are now, and uploads it as a fresh composition "<talk> (redo 2)". The old
  composition stays in Descript untouched.

## Learned from the published talks (2026-10-16)

The sample: 89 talks on the SREday and LLMday channels, with transcripts read for six of them. The raw data (titles,
descriptions with chapters, lengths) is in `research/youtube-sample.json`.

- **Length.** SREday talks have a median of 19 min (10% shorter than 12.4 min, 10% longer than 24.5). LLMday talks
  have a median of 22.6 min (10% longer than 32). Keynotes run 13-27 min. So `talk_min_minutes: 10` and
  `talk_max_minutes: 30` hold, with flags below about 8 and above about 40.
- **Openings.** "Okay, my name is Laya Anderson" (YouTube's spelling of Leila), "Awesome. So, as you all heard
  earlier, I'm Stephen", "Hello everyone ... My name is Mailo" (Mykhailo), "My name is Jim Hershower". So a
  self-introduction also matches by sound. Some speakers open with a mic check and set-up chat ("You can hear me?").
- **Endings.** "So I think that is my time in my slides. Thank you all very much", or just the last point. Some
  published cuts keep stray words after the end ("Yep." ... "Wait."); the factory drops those.
- **Q&A.** About 28% of published videos still end with a Q&A, usually 1-5 min, at most about 12. Questions also
  come mid-talk during demos ("while it's running, anyone have any questions"), and those stay. Some Q&As are never
  announced ("Great question."), so they get a note and are left for a person to decide.
- **YouTube house style.**
  - Title: `<title> | <speakers> | SREday Austin 2026 Q2`, always with the quarter, at most 100 characters.
  - Description: `<event> - <date>`, "Grab your ticket for the next SREday: https://www.sreday.com",
    "Upcoming SREday CFPs: https://cfp.ninja/?q=sreday&status=open", then "Chapters".
  - Chapters: 9-23 per video (median 13), written `00:00`. The first is "Welcome and Intro" / "Intro", the last
    "Wrap Up" / "Key Takeaways".

## Learned from a real day: PLATFORMday Austin 2026 Q4 (2026-10-16)

The recording: one 8 h 48 min file, 25 GB, 16 talks on the lineup. The analysis took 33 minutes on the M1 Max. The
reference cuts, hand-checked against the transcript, are in `research/platformday-2026-austin-q4.cuts.json`.
`tests/realday.py` scores the draft against them: 13 of 15 within 30 s, and the no-show found.

- **Sound.** Only the left channel had sound, at about -46 dB, so every talk gets the stereo fix. The room is noisy
  (noise floor -41 dB), so starts come from the transcript and are never tightened by the levels: a speaker's first
  words before the mic is live are quiet but still part of the talk.
- **Openings.** The greeting often comes before the name: "Hey, thanks for being here today" 46 s before "I'm Jay",
  "Hi. Good afternoon, everyone" 80 s before "I am Nelma". Before the greeting come mic checks ("Can you guys hear
  me OK?") and set-up chat, sometimes 10+ minutes off-mic. Some speakers never say their name; then the start is the
  greeting ("Okay, how is everybody doing?"). Names are misheard (Raulite, Puligam, Gobi). Nicknames count as
  names (Tim Porter for Timothy), and so do two-letter ones (MJ).
- **Endings.**
  - The talk is over at "And that's our presentation", "That's it from me", "that is everything I have for you
    today" or "Thank you for your time".
  - The Q&A follows, sometimes announced ("I'm gonna take questions now"), sometimes not ("So that's my talk...
    Thank you." and then questions from the room).
  - "Hit me up if you got any questions" and "use my QR code" are invitations, not the Q&A.
  - A Q&A can be longer than the talk (Jay: a 16 min talk, then 13 min of questions).
  - Someone else thanking the speaker ("Thank you for the presentation") means the talk is over.
- **The day.** The camera ran through a 47-minute lunch break; a pause of 3+ minutes ends a talk. Slots get swapped
  and speakers miss their slot: Anderson Gobbi spoke in Abhijeet's slot, and Abhijeet's talk never happened. When a
  later talk's speaker clearly introduces themselves in a slot, the planned talk is not forced into it.

## Files

| | |
|---|---|
| `settings.yml` | everything tunable; `settings.local.yml` (git-ignored) overrides it per machine |
| `live_factory/events.py` | brands, events, talks.csv rules and the schedule, rebuilt as `_event_template/_build/generate.py` builds it; keep the two in step |
| `live_factory/analyse.py` | the Whisper pass |
| `live_factory/segment.py` | the brief, the draft, and cuts.json checks |
| `live_factory/stereo.py` | one-sided stereo detection |
| `live_factory/pipeline.py` | state, gates, the Descript steps (from the Conf42 factory, with its credit guards and ledger) |
| `live_factory/descript.py`, `media.py`, `machine.py` | copies of the Conf42 factory's modules, adapted; fix bugs in both |
| `live_factory/app.py`, `dashboard.html` | the local app |
| `tests/smoke.py` | offline test on a synthetic day (`--no-whisper` works anywhere) |

Work files live in one folder per event, named after the conference so several can run side by side:
`<Desktop>/video factory - LLMday Austin 2026 Q4/` (`event_folder` in settings.yml; `work_root` in settings.local.yml
puts them on another disk). Opening an event in the app makes no folder; the first add or save does. Folders from
before 2026-10-17 (`<Desktop>/live-factory/<event id>/`) are still found. The ledger of paid Descript edits is
machine-wide, in `live-factory/local/` (git-ignored). Each folder holds:
- `state.json`, which a running job owns;
- `cuts.json` (gate 1) and `reviews.json` (gate 2), the only files the app writes;
- `in/`, `rec/<id>/`, `cuts/`, `out/` and `srt/`.

talks.csv is only ever read: it is a Drive export.

## Learned from a real day: LLMday Austin 2026 Q4 (2026-10-17)

Two recordings, before and after lunch: r1 4 h 27 min (12.7 GB), r2 2 h 48 min (8 GB), 11 talks on the lineup. The
analysis took about 25 minutes on the M1 Max. Claude's checked cuts are in `research/llmday-2026-austin-q4.cuts.json`.
`tests/realday.py llmday-2026-austin-q4` scores the draft: 7 of 11 within 30 s.

- **The sound can change sides during the day.** r1 has its sound on the right channel until about 3:19 and on the
  left after it (Leila Anderson's talk). The recording's note only names the side most of the day used. The stereo fix
  is measured per talk, so each talk gets its own live channel.
- **Room noise close to the speech.** In r2 the breaks sit at about -36 dB and the talks at about -30 dB. The level
  check (12 dB over the 20th-percentile floor) therefore called most of every afternoon talk "quiet" and flagged
  "hardly any sound" and dozens of drop-outs. A quiet stretch where the transcript hears 60+ words a minute is now
  speech, not a drop-out, and becomes a note instead of a flag.
- **Where the draft went wrong (4 of 11):**
  - An unannounced Q&A right after "Thank you." (Nick Smith) and "And thank you. I am happy to take question"
    (Maryam Astero). The draft ran on to the next talk, through the MC and the team's conference promo.
  - An audience discussion straight after the last line (Nate Lemos, "...that's not enough anymore"). The draft kept
    6 minutes of it.
  - A closing cut 40 s early ("And yeah, thank you" from Vashishtha Patil).
- **Not a talk:** a team member's promo of the other conferences (QR codes, upcoming events) filled a gap while a
  speaker set up. It's never cut as a talk.
- **The day ran ahead.** Speakers who didn't show (Jesse, plus Nikita and Jonathan Malkin, drafts not on the lineup)
  shifted the afternoon, and talks ran 12-14 min (Matt Barge, Nate Lemos), so "only 13 of 30 planned minutes" is
  expected on such a day. The recording ends at the MC's "half an hour break, see if the last two speakers make it".
- **Openings:** "Hi. Good morning, everyone" before the name; "All right. You know, they say..."; names misheard
  (Ajbirra = Azmeera, Nehouse = Niehaus, Amostero = Astero, Kone Lutemulai = Kunle Olutomilayo).
- **Starting the factory:** an app left running from the day before didn't know events move to `past/` and opened on
  DevOps Not Dead London. See the app notes under "A day of recordings".
- **The disk can fill during a run (2026-10-18).** Other work on the same Mac (Reliaburger builds) took the free space
  from 67 GB to 11 GB while the talks were being cut. The factory checks space only when a run starts, so Claude
  stopped the run by hand at a safe moment (an upload in progress, no paid edit), waited until 25+ GB stayed free for
  10 minutes, and ran it again. The new run picked up where the old one stopped:
  - it cancelled the leftover Descript upload ("cancelled 1 Descript upload(s) left over from a stopped run");
  - it cut Kunle again from scratch;
  - it uploaded Geoff again.

  Stopping mid-upload leaves a short empty composition with the talk's name in Descript (Geoff: 6 s, next to the
  real 25 min one). The AI edit worked on the right one, but the empty one has to be deleted by hand.
- **Fixed (2026-10-19):** each talk checks the space again right before it cuts and before it downloads Descript's
  export. On a full disk it stops with the red banner (no retries), and the next run carries on.
- **Edit reports:** Colby cost 11.6 credits (250 um/uh muted, 16 long gaps shortened) and Geoff cost 9.7 (99 muted).
  Descript sometimes doesn't confirm the limiter's knee value; listen for it in the review.
- **The whole night (2026-10-19 07:37):** all 11 talks were edited and are waiting for review, for 119.8 credits in
  all (7.7-13.5 a talk). The factory's own retries (3 attempts, 2 min apart) got through Descript's hiccups:
  - Kunle's import "was cancelled" by Descript;
  - 429 "A job is already running for this project";
  - Rodney's upload came back with "no usable video (6 s)".

  Every failed or cancelled import leaves a composition with the talk's name. There were 5 empty 6-second ones (Geoff,
  Kunle, Maryam, Matt, Nick) and Rodney's: his upload looked empty when checked, then filled in later as a full-length
  **unedited** twin (38:07, next to the edited 34:26). The morning report counted only three - the factory's own
  list is the one to trust. The AI edit always picked the real one.
- **Leftovers can't be deleted by the factory:** Descript's API has no way to delete a composition (its only DELETE is
  for jobs). Instead, the end of every run (or `python -m live_factory leftovers <event>`) lists them in the log and as
  a banner in the workbench: name, "empty" or "unedited duplicate", and length - the name is the same as the real
  talk, so delete by length. A redo's earlier version is never listed (it's kept on purpose).
- **Whisper hears a mic left far away.** The "transcript hears speech" rule above let a talk recorded 35 dB too quiet
  pass as fine (caught by the smoke test). A talk more than 15 dB under its recording's usual talk level is flagged
  "much quieter than the rest of the recording" even when the transcript understands it. None of the Austin talks
  gets this flag.

