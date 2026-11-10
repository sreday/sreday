# Postmortem: PLATFORMday San Francisco 2026 Q4, LIVE talk factory (2026-11-10)

Event: Friday 2026-11-06 at Sentry, 45 Fremont St, two rooms ("All Hands" = main room, "Boardroom" = mini room).
Factory run on Elephant (Windows, RTX 5090) on 2026-11-10. Blameless.

## Summary

Two Google Meet recordings came in:

- r1 "PLATFORMday San Francisco 2026 Q4 - 2026_11_06 07_13 PST - Recording.mp4": 8:00:05, it stopped at Meet's 8 h limit.
- r2 "ijz-bvxm-orj (2026-11-06 11_03 GMT-8).mp4": 6:26:28.

19 of the 24 lineup sessions were cut and edited in Descript, and they're waiting for review. 5 were not cut:

- 2 have muted sound.
- 2 were never recorded.
- 1 speaker didn't show.

No failed uploads and no leftovers.

## Timeline (2026-11-10, UK time)

- ~20:30 Marek: "activate live talk factory for PLATFORMday San Francisco 2026 Q4". Both files were added as day 1,
  track 1: one frame of each showed the "All Hands - SFO" Meet tile.
- 20:40 `analyse` started (detached with Start-Process). The first try died at once: `cmd /c "set PYTHONUTF8=1 && ..."`
  makes the value "1 " (trailing space) and Python refuses to start ("invalid PYTHONUTF8"). Restarted without the
  `set`. Both recordings (14.4 h) were analysed by 21:09 (~29 min).
- `draft` was far off. Its worst guess was an 87 min "Sujeeth Pasham" cut in r1, and Wassim was "not found".
  - Lining up r1 and r2 by text found almost no shared speech.
  - A contact sheet of r1 frames showed why: **r1 switches from All Hands to "Boardroom - SFO" at ~2:48**. So r1 is
    the main room for the three keynotes and then the mini room. r2 is the main room from 11:03 to the close.
- What the levels and transcript showed:
  - The mini room was muted in Meet from r1 2:58 to 3:34 (-90 dB floor, Matthew Senick).
  - It was muted again from 3:34 to 3:56 (Roy Derks), apart from a few seconds.
  - Hariharan Subramanian and Sujoy Datta Choudhury came after r1's 8 h limit.
  - Sujeeth Pasham was a no-show: at r2 6:20:57 the MC says "hopefully by the time Sujit comes", then closes the day.
- Claude wrote cuts.json from the transcripts and frames (19 cuts, 2 medium), and gave Marek the table plus 2 questions.
- Marek: "all OK, the bottom line is that we don't need the recording on 'hello sf' ... Ship to descript what you
  have - misrecorded stuff just skip". All 19 were approved on his word.
- 22:20 `run` started detached (Start-Process). Claude watched the log and the process (Dallas action 4). The factory
  waited out Descript's "a job is already running" 429s 16 times.
- 23:35 the run finished: 19 talks ready for review, `leftovers`: none.

## What went well

- The detached Start-Process run survived the whole 75 min, with no silent stop like Dallas 03:11. This is evidence
  for Dallas action 5: starting the job outside Claude's shell keeps it alive.
- No failed uploads and no leftover compositions.
- Frames solved the room question in one look. Reading the Meet tile name ("All Hands" / "Boardroom") is the fastest
  way to tell rooms apart.
- The -90 dB floor in levels.json made the muted stretches certain (the Dallas lesson worked).

## What went wrong, and why

1. **4 mini-room sessions lost**: Matthew Senick and Roy Derks (sound muted), Hariharan Subramanian and Sujoy Datta
   Choudhury (not recorded).
   - Why? The mini room was recorded by one Meet call that ran from 07:13. Meet stops recording at 8 h, so it ended
     at ~15:13, before the last two sessions. The Boardroom mic was muted in Meet for its first hour.
   - Why? Nobody restarted the recording before the limit, and nobody checked the mic level at speaker changes.
   - This is Dallas action 1 (the venue recording checklist), which is still open. Same family of loss, second event
     in a row.
2. **One recording held two rooms**, and the factory's model is one recording per track.
   - The brief put mini-room talks in the track 1 brief, and `draft` guessed across rooms. That cost ~30 min of
     detective work.
   - It didn't make cuts wrong, because cuts.json may use any recording. But nothing flags it.
3. **The brief's running order was ~1 h off.**
   - It showed the keynotes at 9:00-10:30 *before* "Doors open 10:30-11:30" and coffee 11:30. So Gordon showed at
     12:00, but really started at ~11:03.
   - The `opening: true` breaks in metadata.yml (they end at start_time) look like they're placed after the first
     talks instead of before them. Root cause not confirmed: the leading suspect is the brief's schedule walk
     ignoring `opening`.
4. **`publish` skipped SREday Dallas** with "2026-dallas-q4 has no metadata.yml".
   - Why? After `make archive` moved Dallas to past/, a local site build left an ignored `static/` folder at the old
     top-level path, and `events.event_path` took any existing folder as the event.
   - **Fixed.**
5. **`python -m live_factory add --help` crashed with a traceback.** It read "--help" as an event name. **Fixed.**
6. **Claude's launch line `set X=1 && cmd` gives the variable a trailing space.** Lesson added to the skill.
7. **Credits per talk aren't recorded.** state.json's edit reports have no credit figure, so this run's cost is
   unknown from the logs. Check it in Descript's usage page.

## Same bug elsewhere

- `event_path` is the only place that picks between the top-level and past/ folders. `all_events` already requires
  metadata.yml. Nothing else in live_factory does the isdir test.

## Actions

Done now (pushed with this postmortem):

- `events.event_path`: the folder with a metadata.yml wins, so a leftover build husk no longer hides an archived
  event. Tested: Dallas resolves to past/, SF to the top level, and `publish` now includes Dallas.
- `<command> --help` prints the usage instead of a traceback.
- Skill: two lessons.
  - One recording can hold several rooms: check the tile name on a frame contact sheet before trusting `draft`.
  - Windows detached launch: no `set X=1 &&`.
- README "Learned from a real day" section, MANUAL and MANUAL-CLAUDE Changelog.
- `tests/smoke.py --no-whisper`: all good.

Open:

| # | Action | Owner | Why |
|---|---|---|---|
| 1 | Venue recording checklist, now overdue. Do these for every room: one recording per room, restart it before Meet/Zoom's 8 h limit (e.g. at lunch), check the mic level at every speaker change, keep a phone backup on the room mic. Put it in the T-minus playbook (flows scrapbook). | Marek / event team | Wrong 1 (and Dallas wrong 1) |
| 2 | Ask Sentry / the team for any other recording of the Boardroom between ~10:10 and 11:10, and from ~15:10 to the end (phone, Meet cloud copy) | Marek | Wrong 1 |
| 3 | Find why the brief's running order puts the `opening` breaks after the keynotes, then fix it (`draft` uses the same walk) | Claude, next factory job | Wrong 3 |
| 4 | `analyse` reads the room tile name from frames, or `add` accepts room changes inside one recording (a track per time span), so the brief and `draft` split rooms | Claude, proposal first | Wrong 2 |
| 5 | Record Descript credits per talk in state.json | Claude | Wrong 7 |
| 6 | Dallas action 3 still open: `analyse` flags -90 dB stretches over 60 s, and the brief lists them | Claude | Found by hand again |
| 7 | Dallas actions 6, 8, 9 still open (pin faster-whisper/ctranslate2, setup.cmd from Claude's shell, `draft` overlaps) | Claude | Carried over |

## Numbers

- Recordings: r1 8:00:05 + r2 6:26:28 = 14.4 h. Analyse ~29 min on the RTX 5090.
- Lineup 24 sessions:
  - 19 cut (13 main room, 6 mini room).
  - 2 with muted sound, 2 not recorded, 1 no-show.
  - The "Hello San Francisco" intro was left out on Marek's word.
- Run: 22:20-23:35 (75 min) for 19 talks, 2 at a time. 16 Descript 429 waits, 0 failures, 0 leftovers.
- Descript credits: not recorded (action 5).
