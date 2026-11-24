# Postmortem: LLMday Tel Aviv 2026 Q4 (llmday-2026-tel-aviv-q4), 2026-11-24

Blameless. Facts from state.json, the run log and this session. Machine: Elephant (RTX 5090).

## Summary

The event was on 2026-11-17 and the factory ran on 2026-11-24. There was no whole-day recording: the TLV partner sent
one Premiere-edited MP4 per talk. 9 talks went to Descript, sent whole as Marek asked, and are waiting for his
review. No leftover compositions. Lost: the panel (not recorded) and Haggai Zohar's workshop (not sent).

## Timeline (2026-11-24, local time)

- 17:31 Files on the Desktop in `TLV`: 6 MP4s, plus 9 Drive zips. 4 of the talks (Opening, Yev, Noa, Liza) were
  inside the zips, mixed with Premiere cache, masks, the project file, headshots and sound effects.
- 17:34 Claude unzipped only the 4 talk MP4s into a new folder, `TLV\videos from zips`.
- `add` without `--track` was refused: "which track is this recording?" (day 1 has a workshop room). Re-run with
  `--track 1`.
- 17:35 r1-r10 added, then `serve` and `analyse` started. 17:46 analysis done (10 files, ~5.8 h of video, ~11 min).
- `draft` guessed wrong:
  - Sharone on r2 (Yev's file), 6 min;
  - Noa at 6.5 min of a 15 min file;
  - Liran starting at 5:22;
  - Yev and the panel "not found".
- Claude wrote the cuts from the file names and the self-introductions, with the closing Q&As cut.
- r10 `BG-005.mp4` has no speech: the frames show the LLMday + DATA background loop.
- Marek: "no panel recording then. Just run them, don't trim too hard - it was already edited by the TLV partner".
  Claude changed every cut to the whole file (0 to the ffprobe duration, Q&As kept), approved all 9 and started `run`.
- 21:10 Run finished: 9 talks ready for review. Descript's "a job is already running" 429s were waited out.
  `leftovers`: none.

## What went well

- `add`, `analyse`, `run` and the status page worked unchanged on a new input shape (per-talk files).
- Detached `Start-Process` for all three long jobs; nothing stopped silently.
- No failed uploads, no leftovers. Every edit report says the AI touched only its own composition.

## What went wrong, and why

1. **`draft` mismatched per-talk files.**
   - Why? It walks the schedule along one long recording per track, but here every file was a separate talk.
   - Root cause: there is no "one file = one talk" mode. Harmless this time, because Claude writes cuts.json by
     hand, but `draft` and the brief were noise for this event.
2. **False "swapped slot" flags on 3 talks.**
   - Why? Yev's talk has no abstract. Its keywords were just "cluster" and "talks" (weight 6, passes the `total >= 6`
     gate), and any talk that says both words scores 100%.
   - **Fixed**: `content_scores` also needs 3 distinct keywords. Re-checking the TLV cuts gives 0 swapped-slot flags
     (was 3); smoke test all good.
3. **Footage arrived mixed with editing junk** (Premiere cache, masks, project file, sound effects, the background
   loop), split across Drive zips.
   - Claude had to pick it apart by hand. r10 (the background loop, 67 min) was copied in and analysed for nothing.
4. **Panel not recorded; the workshop was not sent.** This is the same family as the Dallas/SF losses: no recording
   checklist at the venue (Dallas action 1, still open).
5. **Edit reports show "Track gain 300%"** on files the partner already mixed.
   - The limiter is on, but loudness should be checked at review. Unknown if it is a problem. Look at it in Descript.

## Same bug elsewhere

- `content_scores` is the only place that scores topics. `draft` uses it too, so the fix also stops `draft`
  favouring a short-title talk.

## Numbers

- 9 talks, ~2.8 h of talk video in Descript (3.97 to 29.6 min each).
- Analysis ~11 min for 10 files. Run 9 talks, 2 at a time, finished 21:10.
- Credits: not recorded in state.json (SF action still open). Check Descript's usage page.

## Actions

Done now:

- `segment.content_scores`: needs at least 3 distinct keywords (finding 2). Tested on the TLV cuts and
  `tests/smoke.py --no-whisper`.
- Skill, README ("Learned from a real day: LLMday Tel Aviv"), MANUAL and MANUAL-CLAUDE Changelog: edited per-talk files
  are matched by name and sent whole; skip background loops and Premiere cache.

Open:

| Action | Owner | Finding |
|---|---|---|
| Venue recording checklist incl. panels (Dallas action 1) | Marek / event team | 4 |
| Ask partners for the talk MP4s only (no Premiere project/cache) and for the workshop if it was recorded | Marek | 3, 4 |
| Check loudness of the partner-mixed talks at review (track gain 300%) | Marek | 5 |
| Proposal: `add --per-talk` that writes one whole-file cut per recording, named from the file, skipping `draft` | Claude, after Marek agrees | 1 |
| Record credits per talk in state.json (SF action) | Claude | numbers |
