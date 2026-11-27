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

- **Extra footage for trimming by hand** (until 2026-11-27). The automatic start and end were right on about 80% of
  talks, so every clip got 30 s before and 60 s after for trimming in Descript. Since the sharp ends (below) the
  factory sets the exact words itself and `pad_before_seconds` / `pad_after_seconds` are 0.
- **Black bars.** When the slides sit in a steady box smaller than the frame, they are zoomed to *fit*: they grow
  until they touch the frame's edges, so nothing is cut off whatever their shape. On PLATFORMday Austin, Thomas
  Cameron's 1600×810 slides became a 1.2× zoom. A talk where the picture changes (camera and slides, or dark slides
  that fool the detection) is left as it is.
- **Flag for redo.** In the workbench's Descript table, say what's wrong and press "flag for redo" (or run
  `redo <event> "<talk>" "why"`). Fix the talk's cut in gate 1 first if needed. The next Run cuts it again with the
  cut, extra footage and zoom as they are now, and uploads it as a fresh composition "<talk> (redo 2)". The old
  composition stays in Descript untouched.

## Sharp starts and ends (Marek, 2026-11-27)

"I would like you to work extensively now to ensure the beginnings and the ends of talks are super sharp and
production-ready ... Q&A need to always be outed as they don't prove consistency (some questions are bad, mics are not
passed in time, etc.) - so we want to end sharply at the moment when the person finishes, but letting him finish the
sentence. So strip the last sentence if the sense allows it ... I just don't want someone to finish mid sentence." Then:
"full auto, so sharp cuts with guardrails".

`ends.py` runs between the lineup match and the cut (`ends <event>`, and by itself at the start of `run`). The coarse
edges in cuts.json come from the whole-day transcript, to the second; this step sets the exact words.

**Listening closely.** Whisper large-v3 (beam 5, word timestamps) listens to a window at each edge:
- 75 s before to 90 s after the coarse start;
- 180 s before to 60 s after the coarse end.

The words are cached in `rec/ends-words/`, so a change of rules costs nothing. Each setting came from what went wrong
on SREday Dallas:
- **The voice detector keeps its default 2 s of silence**, as the whole-day pass (`analyse.py`) does. At 400 ms it
  chopped the speech into scraps, dropping quieter greetings and the punctuation with them.
- **No carried context.** With it, Whisper looped through quiet minutes, writing one sentence nine times.
- **No prompt.** A punctuated prompt came back as words in the quiet: "So, today I'm going to talk about
  reliability."
- **No `hallucination_silence_threshold`.** It dropped Girish Konda's real "Hey, good afternoon, I'm Girish".

Whatever the close listening misses, the whole-day transcript fills in (`fill`): on some windows it went silent for
the last 100-160 s.

Whisper's own inventions are dropped:
- the "Thank you for watching" echo;
- a third repeat in a row;
- zero-length words;
- a lone "you";
- unsure words at the room's quiet level;
- unsure lines the whole-day pass has no trace of (Girish's start had an invented "Hello, everyone.").

A long run without punctuation, typical of a far room mic, is cut at its own pauses and wherever a greeting, a
thank-you or a Q&A phrase begins.

**The start.** It comes after the MC's last words: "please welcome", "I'll turn it over to my good friend Kevin", or
the speaker named in the third person. It skips what comes before the talk proper:
- mic checks ("can you guys hear me?");
- the laptop and the tech desk ("let me share my screen", "look at the power button", "is someone connected to the
  Meet?");
- "are we good to go?", "I think we can start", "I think I'm ready";
- bare words: "Okay." / "All right." / "Thank you, thank you." (to the MC);
- short scraps.

It starts on the speaker's own greeting or name ("Thanks, everyone, for joining.", "Hi, I'm Kevin."). A later greeting
wins over chatter: "Good morning. / Can y'all hear me? / ... / Thank you for coming, this is Krishna" starts on the
second. A greeting inside a run of set-up ("toss it ... no thank you yeah well first off") starts on its word. A cold
open after a pause ("So my talk is about ...", "Who here ...?") is a sure start; one with no pause that opens on "and"
/ "but" is mid-sentence and flagged.

**The end.** First, where the talk proper is over:
- the Q&A opening ("any questions?", "but uh questions?", "I'll stop there and answer any kind of questions", "we
  have a couple minutes for questions", "that concludes"), taking the last opener of a cluster;
- the room ("great question", "thank you for the presentation");
- the MC ("give it up for Jerry", the speaker thanked by name).

Then the closing thank-you in the last lines is the end, even inside a run ("... thanks for listening and | I guess we
have time for questions"). Of "Thank you. / Thank you." in the applause, the first one counts.

Without a thank-you, it steps back to the last sentence that may end a talk:
- finished (. ! ?), or cut where the Q&A begins;
- at least 3 words;
- not hanging on "and" / "so" / "the" / "any" ...;
- not a question to the room;
- not "so yeah" / "any questions?" / "happy to take questions";
- trailing filler goes ("... that should be all yeah um" → "... that should be all").

A close that trails off ends on the firm sentence before it. Marek, on Krishna Reddy's sample: "I'd prefer to always end
on the firm sentence, if it's followed by some mumbling and stuff". A trailing-off close is one of these:
- Whisper heard no end to it: a run with no punctuation, or one cut only where the Q&A begins;
- filler was trimmed off it;
- 8%+ of its words are "um" / "uh";
- its words are unsure.

The firm sentence is the last one within 30 s that Whisper ended with . or !, of 5+ words, few ums and sure words, with
a pause after it. Krishna's "... implement that inside the company." now ends his talk instead of "... that's security
concern um that should be all". A closing thank-you is never stepped back from.

It steps back at most 60 s. Where nothing passes, the end is proposed on the last piece before a 1.2 s pause and
flagged, never guessed.

**Placing the cut on the sound.** Whisper's word times can be a second early: Kevin Brown's cut lost "slash teams".
So the end goes into the first real pause (200 ms of quiet) after the last word, 0.8 s in and never into the next
word. The start goes just after the last pause before the first word, 0.4 s ahead of it, and never further back than
the word before it. When there is no gap at all, that's a note in the brief; the check after the cut decides.

**Guardrails.**
- **Flags.** A talk is flagged, and stops before cutting, when its edges are not sure:
  - no greeting or name at a start that isn't after the MC;
  - a lowercase start, or someone else introducing themselves;
  - set-up talk left in the first line;
  - an end that is not the closing words before the Q&A;
  - 20 s+ and 40+ words of speech dropped before the agreed end;
  - a question in the whole-day transcript minutes before the end (a mid-talk pause, or a Q&A left in: PLATFORMday
    SF, Wassim Melakhessou's "great question" came 8 min before his real end).

  Claude reads `ends_brief.md` (per talk: both edges, why, and the sentences around them) and confirms or moves them
  in cuts.json: `"ends": {"start", "end", "by": "claude", "why"}`. A change there is a changed cut.
- **The check after the cut** (`verify`, before any Descript credit). The cut's first and last 20 s are heard again,
  without the voice detector and with a second of silence after (Whisper drops a file's last words). The cut must:
  - start on 2 of the planned first 3 words and end on 2 of the planned last 3 (a quiet last "thank you" heard as
    sound only is fine);
  - have no Q&A in its last seconds;
  - not start or end in the middle of a sound where the word there isn't the planned one.

  Otherwise the talk stops before Descript.

**On the two days at hand** (the factory's own cuts, rules only, before Claude):
- **SREday Dallas 2026 Q4 (14 talks, one far Zoom room mic):** 8 sure and right, 6 flagged. The flags:
  - Chris Hankla's opening lost (sound from mid-sentence);
  - the workshop's sound coming back mid-sentence;
  - Jeremy Newhouse's room mic dying;
  - two ends with no punctuation heard;
  - 42 s dropped after Kevin Brown's QR code: the MC's question.
- **PLATFORMday SF 2026 Q4 (19 talks):** 10 sure, 9 flagged.
- **The check after the cut, on stand-ins** (each cut's first and last 25 s) of 17 talks: it caught every mistimed
  edge on the way. It stops only cuts with a real problem; on Dallas that left the two starts that run straight on
  from "thank you" / "okay", which the rules had already marked.

**What the team's published finals showed** (the SREday / LLMday / PLATFORMday channels, 2026 talks; Whisper on each
video's first 80 s and last 150 s; `research/published-ends.json`):

- **The sample:** 112 talks. Of the 315 published 2026 talks on the channels, the first 112 were transcribed before
  the GPU was needed elsewhere.
- **Starts:**
  - a greeting: 47% ("Hello everyone, my name is AJ.");
  - the name, a thank-you or "so today": 19%;
  - a cold open straight into the topic: 17% ("So I've just returned from Braga ...", "Who out here feels like ...?");
  - set-up still in: 15% ("All right." / "Good." / "Okay, so I assume we can start", "Okay, thank you. I think we
    can start", "Okay, let me just adjust this ...", "I hope you can hear me").

  The factory now cuts that set-up off. A cold open stays a start.
- **Ends:**
  - the closing thank-you: 41%;
  - the last point with no closing words: 26% ("That was my point.", "So, yeah, that's us.");
  - a closing line: 5% ("That's it.", "That's all for me.");
  - with no punctuation in Whisper's reading, or cut mid-sentence: 18% ("... we choose Azure or AWS first, if we can,
    or");
  - on opening the Q&A: 4% ("If you have any questions, we will answer them here");
  - with the room or the MC still in: 2% ("Thank you for the presentation.", "Thank you very much, Greg.").

  Reading the 70 transcribed first, a few more had a Q&A answer still in ("So yeah, I don't know about Vault, but could
  look into that. Thanks, everyone."). Marek's rule wins over those: the Q&A is always out, never mid-sentence.
- **Around the words:**
  - before the first word: a median 0.0 s (often cut right on it; 90% under 0.6 s) - the factory leaves 0.4 s;
  - after the last word: a median 1.1 s (10% under 0.1 s, i.e. clipped) - the factory leaves 0.8 s, inside the pause.
- **Whisper's echo:** "Thank you for watching." over and over in a final silence is Whisper, not the talk. The
  factory drops it.

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
| `live_factory/audiosync.py` | separate sound: fingerprints, placing it on the videos, choosing it per talk |
| `live_factory/ends.py` | sharp starts and ends: the speaker's first words, the last sentence before the Q&A (word-level Whisper at each edge) |
| `live_factory/selfcheck.py` | autopilot's gate 2: the factory's own look at each final (edges, silences, black picture, loudness, length) |
| `live_factory/ytupload.py` | autopilot's upload: the brand's channel, private until 5 weeks after the event, thumbnail, captions, playlist |
| `live_factory/saturation.py` | saturated (clipped) sound: finding it per minute, rebuilding the peaks (BetterDeclipper) |
| `live_factory/picture.py` | the picture pass: deck pages over the slides, builds, night mode, the before/after sheet |
| `live_factory/pipeline.py` | state, gates, the Descript steps (from the Conf42 factory, with its credit guards and ledger) |
| `live_factory/descript.py`, `media.py`, `machine.py` | copies of the Conf42 factory's modules, adapted; fix bugs in both |
| `live_factory/app.py`, `dashboard.html` | the local app |
| `tests/smoke.py` | offline test on a synthetic day (`--no-whisper` works anywhere) |
| `tests/av_smoke.py` | offline test of the picture and sound passes on a synthetic screen share, deck and recorder |

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


## Learned from a real day: SREday Dallas 2026 Q4 (2026-10-29)

- **A Zoom recording of the whole day** (one 8:43 h file, 2384x1542, speaker view switching to screen shares) works
  like a camera: `add ... --day 1`, `analyse`, match. Zoom only records what the room shares, so expect stretches with
  no picture or no sound:
  - Michael's talk is a blank grey frame from start to end (he shared nothing): **audio only**, flagged "picture blank".
  - A no-slides panel (6:01-6:22) recorded no sound at all; only its last line ("our time is up") made it.
  - Two talks start mid-sentence after a silent stretch (Chris's keynote ~6 min lost; Mark's workshop after 103 s).
    Look at the stills: slides on screen with a silent level line mean the talk had started.
  - The recording stopped 5 min into the closing panel.
- **Windows PC with an RTX 5090 ("Elephant")**: Whisper large-v3 on CUDA did the 8.7 h day in about 15 min (30 min of
  audio in ~50 s), cuts go through NVENC. `setup.cmd` would not start from Claude's shells here; its steps by hand
  (venv with `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`, `pip install -r requirements.txt`, the two
  `nvidia-*` CUDA packages, `python -m live_factory setup`) did the same. An existing `DESCRIPT_API_TOKEN` (from the
  Conf42 factory) means setup asks for nothing.
- **PyAV 19 broke faster-whisper 1.2.1** (`open() got an unexpected keyword argument 'metadata_errors'`):
  requirements.txt now pins `av<16`.
- The "sounds like <other talk>" check gave a false alarm on an 11-min keynote with generic words (Chris vs Dusan):
  the slides on screen settled it.
- **Windows: "Access is denied" on the state.json swap** killed the first run (the workbench was reading state.json at
  that moment). `machine.swap_in` now retries `os.replace` for up to 5 s, for every JSON file the workbench and a run share.
- **Q&As are cut, every time** (Marek: "cut Q&As, leave margin in descript"): the 60 s after the cut is enough to put
  a good question back in Descript. Low-confidence talks go to Descript too, for Marek to check there.
- **The host's own talk was not in the lineup** (Jeremy Newhouse, Evolv, 12:00): added to talks.csv, lunch shortened
  to 30 min in metadata.yml, and the row handed over for the WWW sheet.
- Credits: 7.7-15.5 per talk, about 120 for the 12 edits paid on the day. One failed upload left an unedited
  full-length twin (Isaac Rowaiye, 0:13:41) to delete in Descript.
- A run and the workbench once stopped together with no error in the log (something outside closed them). Starting
  `run` again picked up where it stopped; credit guard 2 skipped the edit Descript had already finished.

## Learned from a real day: PLATFORMday San Francisco 2026 Q4 (2026-11-10)

Two Google Meet recordings, two rooms (All Hands = main, Boardroom = mini). Postmortem:
`postmortems/2026-11-10-platformday-2026-san-francisco-q4.md`.

- **One recording can hold two rooms.** r1 showed the main room for the keynotes, then the Meet call switched to the
  Boardroom (~2:48 in) for the rest of the day.
  - The track given at `add` is only used by the brief and `draft`. Cuts may use any recording, so cut each talk from
    whichever recording shows its room.
  - To tell the rooms apart, read the tile name ("All Hands - SFO" / "Boardroom - SFO") on a contact sheet of
    `rec/<id>/frames`.
- **Meet stops recording at 8 h.** A call started at 07:13 lost the last two mini-room sessions. Restart before the limit.
- When `draft` makes 80+ minute cuts or can't find a keynote, the room/recording split is wrong. Sort that out before
  reading the brief.
- The brief's running order can be off by an hour (opening breaks placed after the keynotes, open action). Trust the
  transcript and the MC's handovers over the clock.
- An archived event (`past/`) whose old top-level folder still holds an ignored build `static/` is now found by its
  metadata.yml.

## Learned from a real day: LLMday Tel Aviv 2026 Q4 (2026-11-24)

No whole-day recording: the TLV partner sent one Premiere-edited MP4 per talk (Drive download, some inside zips with
the Premiere cache). Postmortem: `postmortems/2026-11-24-llmday-2026-tel-aviv-q4.md`.

- **Edited files: one file = one talk, sent whole.** Each file opens with a ~13 s bumper. Marek: "don't trim too
  hard - it was already edited by the TLV partner". Cuts run 0 to the file's end (ffprobe) and keep the Q&A.
- `draft` assumes a day-long recording on the schedule, so with per-talk files it guessed wrong (Sharone on Yev's
  file, Liran from 5:22, Yev "not found"). Match by file name + the speaker's self-introduction instead.
- Drive downloads mix footage with Premiere junk (`Media Cache/*.pek`, `.prmf`, `.prproj`, headshots, sound
  effects). Unzip only `*.mp4` that are talks into a new folder; `BG-*.mp4` was the silent background loop.
- A day with a workshop room needs `--track 1` even when only main-room files arrive.
- A two-word title with no abstract ("Talk to Your Cluster. It Talks Back!") matched every talk and raised false
  "swapped slot" flags. `content_scores` now needs at least 3 distinct keywords.

## Learned from a real day: LLMday San Francisco 2026 Q4 (2026-11-24)

Two whole-day Zoom recordings (main room r1, meeting room r2, ~10 h each), from Drive. Cuts:
`research/llmday-2026-san-francisco-q4.cuts.json` (23 cut, 3 no-shows).

- **The Drive zips of a Zoom recording hold only the `.m4a`, chat `.txt` and `.cc.vtt`** - the room videos come as
  separate `.mp4`s. Add the mp4s only. Zoom's separate m4a carries the same mute as the video: when the room mic was
  muted in Zoom (r2 silent at -90 dB until 2:29:50, Devang's first ~6 min), the m4a is silent too - nothing to recover.
- **A talk can move rooms because the audience moved**: the meeting room was sent to the main room at 16:30 and
  Vaibhav spoke there (r1). After that the empty room's recording only has chatter plus faint **bleed from the main
  room** - Whisper lines in r2 that echo r1 a few seconds apart (Sebastian's "what it takes to be a founding
  engineer"). Compare with the other recording before calling such a stretch a talk.
- Misheard names again: "Mel Parry" = Paari, "Vepa" = Vaibhav, "Kassu Big" = Qazybek, "I'm solo" = Saurabh,
  "I am of niche" = Avneesh, "Sude" = Sujoy, "Fumi Lashie" = Funmilade.
- **A dot in a speaker's name broke the Descript upload** ("Funmilade A. Lesi": "Uploaded file has invalid or
  unsupported media content", 6 tries). Descript reads the media type from the media name, which had no extension.
  `descript.import_file` now always ends the media name in the file's extension.

## Autopilot and YouTube (Marek, 2026-11-27)

"We need to automate the rest of the process, up to the video upload, changing all info, putting thumbnail, adding
to the playlist, and scheduling (private to public) exactly 5 weeks after the conference ... instead of asking for
permission to run this further, you'll ship it and write a post-mortem ... ask for forgiveness not for permission".
His choices: the sign-in reuses the Conf42 factory's OAuth app, with one token per channel; the release is at 09:00
local; the links go into the WWW sheet.

**`autopilot <event> on`** (cuts.json `"autopilot": true`; settings `autopilot: false` by default, so the two gates
stay for the manual flow):
- **Gate 1:** `approve <event>` - Claude approves; a cut whose sharp ends are still flagged is held, never guessed.
- **Gate 2:** the review in Descript is skipped. After `qa`, the new `selfcheck` step (selfcheck.py) looks at the
  final MP4:
  - its first and last words heard again (ends.verify on the final, after Descript's edits);
  - its length: 75-102% of the cut - Descript only shortens gaps and fillers;
  - silence: 8 s+ at under -50 dB is a problem (Descript has shortened every real pause), 4 s+ a note;
  - black picture for 10 s+, unless the recording itself was black;
  - qa's loudness and the true peak;
  - the edit report and the chapters, as notes.

  A failing talk is PROBLEMATIC: finished, but never uploaded, with its reasons in `ship report.md`.
- **Upload** (ytupload.py, ported from the Conf42 cloud factory's, which has run since 2026-11-13): at the end of
  `run`, and with `upload <event>`. Per checked talk, once, the video id saved first in `<work folder>/youtube.json`:
  - a resumable `videos.insert`, with youtube.block()'s title and description and tags for the brand, event and city
    (Science & Technology, not for kids);
  - private, with `publishAt` = the event's first day + 35 days at 09:00 in the city's IANA zone (`youtube.timezones`,
    so summer time is right; without an entry the event's UTC offset, noted); `video_release` in metadata.yml
    overrides it; past the release it goes up public;
  - the website's thumbnail (/teasers/<speaker>.png, JPEG above 2 MB) and the Descript SRT as captions;
  - the event's playlist ("SREday Dallas 2026 Q4", made public when missing: its videos stay private until the
    release), kept in talks.csv order.
- **Quota:** 100 uploads a day; thumbnails, playlist items and status reads come from 10,000 units. Hitting the day's
  limit stops the pass with a line; the next pass goes on, and also adds any refused thumbnail, captions or playlist
  item.
- **A redo:** the new video goes up. The old one is deleted only when it was never public; otherwise it stays, and
  the log says so.
- **Channels:** `youtube-login <brand> <client_secret.json>`, by the person in the browser, writes
  `~/.live-factory/youtube/<brand>.json` (outside every repo, never printed). Every pass checks the signed-in channel against
  `youtube.channels` (the id) and the @handle of the event's `youtube_url`, and refuses anything else.
- **The websites (all five site repos):** `generate.py` shows a talk's player and its Watch button only from the
  release (`_videos_out`, the same rule at the event's UTC offset), so the sheet can get the link at upload time. The
  daily build at 04:05 UTC shows them the morning after. A video the team publishes earlier by hand shows from the
  release too; set `video_release` to show it sooner.
- **The WWW sheet:** Claude writes the links through the Google Sheets connector: only empty cells, the row found by
  name.
- **The report:** `report <event>` writes `ship report.md`, which Claude's postmortem is built on: on YouTube /
  problematic / stopped / not cut / missing, the credits, and the Descript leftovers.

Tested by `tests/youtube_smoke.py`: a fake YouTube, the release-time rules, and selfcheck on generated finals.

## Best slides, best sound (Marek, 2026-11-27)

"The standard is always to have the best quality sound and the best quality slides full screen - period. If there's a
demo, then we switch to the inferior recording." And: "always pick the easier path - it's easier to put in slides into
the video than modify the colors."

**Separate sound** (`audiosync.py`). With a wired set-up the room sound is often recorded on its own device while the
camera keeps its built-in mic. Add the sound files with the day (`add <event> ZOOM0001.wav --day 1`; sound extensions
are taken as sound, `--sound` marks any other file; a folder gives only its videos unless `--sound`). After `analyse`
(or `sync <event>`), each sound recording is placed on every video of its day:
- a loudness fingerprint per recording: 100 values a second of the speech band (150-3800 Hz), log, minus its 1 s
  moving mean, digital silence zeroed; cached as `rec/<id>/fingerprint.npy`;
- windows of the sound (120 s, step 60; shorter for a short file, never under 30 s) are slid along each video's
  fingerprint by FFT cross-correlation (normalised); a window counts when it correlates >= 0.25 and stands out
  (peak 9+ standard deviations above the median); once a stretch is found the next window is checked nearby only;
- agreeing windows form stretches with a fitted line `video_t = scale * sound_t + shift` (the drift; a slope further
  than 0.2% from 1 is no clock, so the median shift is used). `rec/<sound id>/sync.json`.
In the cut, the sound covering the clip is laid in with `asetrate` (the drift) and `adelay`, the camera's sound muted
under it and kept where the recorder has a hole; the camera is levelled to the recorder. `audiosync.choose()` keeps
the camera where the recorder is silent (< -55 dB) or its speech stands out of its noise 6 dB less than the camera's.
Per talk, `"sound": "camera"` in cuts.json keeps the camera's own.
Tested: real speech from SREday Dallas placed within 5 ms (a +80 ppm chunk read as +84), also inside the whole 8.7 h
day in 47 s (fingerprints cached afterwards); the synthetic recorder in `tests/av_smoke.py` within 12 ms.

**Deck pages** (`picture.py`). Every talk is looked at every 0.5 s against its deck (`<work folder>/decks/<speaker>.pdf`,
else the event's `slides/<talk page>.pdf` from the deck dump, else a cut's `"deck"`; `"picture": "video"` turns it off).
Per frame: the page's place is found with ORB features (first a scale + move fit - a screen capture - then a full
perspective fit - a camera on a screen), or the last place is reused when the screen is steady. Then a pixel check
on a 16x9 grid of the page at 160x90 decides: cells with content in the page must agree. Things over the page's blank
areas (a taskbar, a headshot in an empty corner) and strips along the sides/bottom are tolerated; a clash anywhere
else is other content. A closer look at 480x270 checks the title text (two bold titles blur alike at 160 px).
Builds: cells (10 px, at 480x270) where the page has content and the screen is still plain, in blocks of 3+, are
painted out in the page's background - the slide appears as the speaker revealed it, never ahead of them (only on a
plain background). Stretches under 2 s are dropped, holes up to 3 s bridged. The pages are rendered at the output size
(written by ffmpeg itself: ffmpeg 9 failed to read some of OpenCV's PNGs) and laid over the video with an ffconcat
track and an `enable` expression in one encode. `look <event>` makes the plans before a run, with
`cuts/<speaker>.picture.jpg` (recording left, result right); plans are cached in `cuts/<speaker>.picture.json`
(`PICTURE_VERSION` in pipeline.py invalidates them).
A page can be on screen several times (Presenter View: the current slide, the next-slide preview, the thumbnail strip,
the projected screen beside it): up to 3 copies per page are tried and the biggest one fully in the frame that passes
wins - never the preview of the next slide. A filmed or soft screen is lined up to the pixel (ECC) and the page blurred
to its sharpness before the closer look, which runs at the page's real on-screen size (at most 480 px). While nothing
matches (a demo), the feature search runs every 2 s instead of every frame.
`"deck_match": "loose"` in a cut picks the page by its layout alone (features; one page clearly ahead) - for a deck
whose PDF has other fonts or line breaks than the presented file (the deck dump converts PowerPoint through Google,
which changes fonts). Builds then show whole. The sheet always shows such layout-only matches, darkened and labelled
"NOT used", so the decision is made by looking.
Learned on SREday Dallas 2026 Q4: Kevin Brown's talk is a Zoom capture of PowerPoint's Presenter View with the
projected screen cut off beside it; his PDF has the same slides with other fonts and line breaks - strict refuses it,
loose finds every page in the console's current-slide box. Girish Konda's talk (Windows taskbar, builds on most slides) gets deck pages on 94%
(19 min in ~2.5 min on the 5090 PC); a sparse page matched a half-built frame of another slide until only content
cells counted; the brightness fit has to be robust (a taskbar or an unbuilt diagram pulled it flat) and bounded
(0.4-2.5); a single hidden line ("Nothing downstream can tell these apart") needs the 480 px look. Bailey Elaine
Laban's deck-dump PDF is another version than the one presented (other wording and fonts): it is refused everywhere
and the notes say so - ask the speaker for the presented deck.

**Night mode.** From the deck pages (per-channel gain screen/deck) or, without a deck, the white point of the screen's
brightest 3% when the picture is screen-like (flat 8x8 blocks). On when blue is under 85% of red. The deck pages need
nothing; the stretches that stay video get `colorchannelmixer` gains when 70%+ of the talk is screen-like (a camera
picture of a warm room is left alone). Cream/beige decks (Bailey's) stay off. Limit: a deck with an orange/sepia theme
and no white at all could read as night mode without its PDF - the before/after sheet shows "video, colours fixed".

Needs `opencv-python-headless` and `pypdfium2` (in requirements.txt; PyMuPDF is blocked by Windows Smart App Control);
without them `doctor` says so and talks are cut as before.

**The check before Descript** (`precheck.py`, step "check" between cut and upload; Marek: "double check the image and
sound were merged correctly, that the bad pilot sound is successfully suppressed - sending it to Descript costs us real
money"). Only cuts with deck pages or separate sound laid in are checked, on the cut FILE: three frames per deck-page
stretch against the very image rendered for it (mean difference <= 8), video stretches against the recording; 8
windows of 0.4 s per separate-sound piece: the cut's sound against the recorder (waveform likeness >= 0.8, lag within
20 ms) and, by least squares, the camera's share in it (the pilot: flagged above 0.12 unless the recorder alone explains
the window, likeness >= 0.97); holes the recorder doesn't cover must carry the camera's sound; the length must match.
On real speech (SREday Dallas, Girish Konda): the good cut 1.00 alike, pilot 0.00; the camera mixed back in at full
level stopped (5 of 8 windows); 60 ms late stopped; the camera at half level (about -23 dB under the recorder) passes
with 0.07 - the limit of what it hears. A failing talk stops with "stopped before Descript" (no retries: the same cut
fails again); `cuts/<talk>.check.jpg` shows its frames; `"precheck": "off"` in the cut overrides after a human look.
Talks uploaded before the check existed skip it. The picture plans of a run are made first, `look_parallel` talks at
once (default a quarter of the CPU threads) - slow is fine (an idle laptop for hours), quality first.

**Saturated sound** (`saturation.py`). Marek, 2026-11-27: "if there's many tracks available to put the audio in, then
we want to pick the better one and discard the bad one; then, if there's NO CHOICE, sample the bad audio (10-30 s of
speech), run through these tools, then judge the effect (understandable speech vs the transcript), and ship for the
whole talk if it's good; if there's no good effect with each, studio sound at 100% in Descript as the last resort.
A sound track saved by the open source tools gets our normal studio sound numbers. Descript's studio sound is the
last resort."
- **Hearing it**: a clipped peak is cut flat at the ceiling; after AAC the plateau is blurred but keeps its shape. Per
  5 s (a local ceiling - an auto gain or a fader can turn a clipped stretch down), every peak above 85% of the
  ceiling gets a parabola fitted to its 8 samples each side; a peak counts as cut when that apex is 25%+ above its
  top AND the top is flat (12+ samples, 60%+ of them within 3% of the top). 40+ such peaks = a clipped minute, 2+
  clipped minutes = a SATURATED talk. Kept per channel and per 10 s. On Girish Konda's speech clipped on purpose
  (then AAC): clean 0 a minute, clipped 11/12/14/20 dB 42/105/376/~1200; limiters holding 14-20 dB of peaks 0-2;
  tanh soft saturation 12 (not flagged - mild); 3 clipped minutes inside his real 19-minute talk found at exactly
  5:00-8:00. All 33 real talks of SREday Dallas + PLATFORMday SF (none clipped): at most 6 in their worst minute.
  Without the flat-top condition the buzzy synthetic voice of tests/av_smoke.py (sawtooth peaks) and limiters read
  as clipping.
- **1. The better track** (no tool needed): `Run.unclipped_runs` - in `sound_for`, the separate recorder's stretch is
  cut into minutes; a minute where the recorder is clipped and the camera is not goes to the camera (a hole in the
  recorder's pieces, which the camera fills). In the cut, a clipped channel next to a clean one that carries the talk
  too (levels within 12 dB) gives way to it (`pick_channel`, ffmpeg `pan`). Scans of the recordings' stretches are
  cached in `<work folder>/rec/clipping.json`.
- **2. No choice: the trial** (`rescue`, `trial`): the worst 30 s of the cut (`worst_stretch`, by the 10 s counts) go
  through every tool in `declip_tools`; each output is judged by the clipping check and Whisper (large-v3, beam 5,
  word probabilities) against the clipped sample itself: at most 25% of the cut peaks left, Whisper's mean word
  probability not lower (-0.005), at most 40% of the words changed (+ twice the rise in word probability: repaired
  words do change), 10+ words heard. The best one that passes (highest word probability) then repairs the talk's
  clipped stretches only (`regions`: 10 s bins at half the clipped rate, merged across 20 s, padded 5 s, in pieces of
  at most 60 s - a declipper reads ONE ceiling per file, and run over the whole talk it found none and changed
  nothing), spliced back with 20 ms fades, turned down to -1 dBFS peak if needed, remuxed with the picture copied.
  Kept only when the talk then has fewer clipped minutes and is no longer saturated. The sample and every tool's
  output stay in `cuts/<talk>.sound-trial/` (with trial.json) for a listen.
- **3. The last resort**: a talk still SATURATED gets Studio Sound `saturated_studio_sound_percent` (100) in its edit
  prompt; a repaired one keeps `studio_sound_percent` (80). "a little clipping" (one minute) is repaired the same way
  but never gets 100%.
- **The tools** run in their own environment `local/declip-env` (torch cu128 is big and must not disturb Whisper's
  CUDA libraries in `env/`; `requirements-declip.txt`; `declip_python` or `LIVE_FACTORY_DECLIP_PYTHON` overrides), in
  a child process (one model load for all the pieces):
  - BetterDeclipper (github.com/Qupci/BetterDeclipper, MIT, pinned to the commit read): a consistent declipper -
    every unclipped sample stays exactly as it is, the clipped ones are rebuilt above the ceiling (NMF plug-and-play +
    A-SPADE); it tells digital clipping, clipping smeared by AAC/MP3 and a limiter apart. 30 s of Girish clipped
    20 / 26 dB: SNR 12.3 -> 25.8 / 6.3 -> 17.2 dB, cut peaks 720 / 1228 -> 0 a minute, Whisper's word probability
    0.704 -> 0.728 / 0.648 -> 0.717, word error vs the clean words 26% -> 18% / 43% -> 21% (the clean speech vs another
    pass of itself: ~21%). His 19-minute talk with 3 clipped minutes: repaired in 71 s on the RTX 5090, no clipped
    minute left.
  - VoiceFixer (github.com/haoheliu/voicefixer, MIT; its model files from the author's Zenodo record on first use): a
    speech restorer that makes the voice again through a vocoder. On our speech it changes the words (65-71% vs the
    clipped input, 68% on CLEAN speech: "the third group of mn alerts" -> "the third group of pokemon") - tried, and
    judged out every time so far. On Girish's clipped minutes it came to 47% changed with no gain in confidence:
    that is why the word limit is 40%, not 50%.
  - ffmpeg's `adeclip` was tried first and left out: it changed clean speech by -30 dB everywhere and restored nothing
    (SNR 15.1 -> 15.0 dB at 14 dB over).
- Settings: `declip`, `declip_tools`, `saturated_studio_sound_percent`; per cut `"declip": "off"` (keeps the sound as
  it is and skips 1-2). `clipping <event>` / `look` list the talks before the run (the sound each talk will use,
  cached in `cuts/<talk>.clipping.json`); the cut step listens to the cut itself again (`t["saturation"]`).
