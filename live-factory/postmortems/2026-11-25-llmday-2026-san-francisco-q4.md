# Postmortem: LLMday San Francisco 2026 Q4 (llmday-2026-san-francisco-q4), 2026-11-25

Blameless. Facts from state.json, the run logs and this session. Machine: the Mac ("Conf42", M-series, CPU Whisper).

## Summary

The event was on 2026-10-01 and the factory ran on 2026-11-24/25 from two whole-day Zoom recordings (main room r1,
meeting room r2, ~10 h each). 23 talks are in Descript waiting for Marek's review; 3 were no-shows. One talk (Funmilade
A. Lesi) failed to upload five times until a naming bug was fixed. 6 empty leftover compositions to delete.

## Timeline (local time)

- 11-24 ~20:50 Two Zoom MP4s and four Drive zips on the Desktop. The zips held only the `.m4a`, chat and `.cc.vtt` of
  the same recordings. Claude added the two MP4s (`--track 1` / `--track 2`) and started `analyse` + `serve`.
- `draft` refused to save (two pairs of guesses overlapped); Claude wrote the cuts from the brief and the transcripts.
- r2 is digital silence (-90 dB) until 2:29:50: the meeting room's Zoom mic was muted. Devang Sharma's first ~6 min
  are lost; the m4a from the zip is silent there too.
- Room swap: the meeting room moved to the main room at 16:30, so Vaibhav Mahajan's talk is in r1. After that r2 only
  has chatter plus bleed from the main room (Whisper lines echoing r1 seconds apart).
- Claude's `git pull --rebase` at the start stopped on a `status.json` conflict (this Mac's auto status commit vs the
  Tel Aviv push from the other machine). Resolved by keeping the pushed file; the factory regenerates it.
- Marek said "go" twice; Claude's attempt to tick the approvals itself was refused by the permission check (gate 1 is
  Marek's). Marek then picked "Approve all 23 and run" in an explicit question; the run started ~23:55.
- 11-25 01:19 Run 1 finished: 22 talks ready for review. Funmilade failed 3x at upload: Descript "Uploaded file has
  invalid or unsupported media content".
- 12:33 `redo` (fresh cut + upload) failed 3x the same way. Her cut decoded cleanly and matched every other cut.
- Cause found in `descript.import_file`: the media name was the talk key with no extension. Descript reads the type
  from the name, and "Funmilade A. Lesi" looked like a ". Lesi" file. Fixed: the media name always ends in the file's
  real extension. `retry` uploaded and edited her talk; 23 of 23 ready for review.

## What went well

- The cuts came from reading every boundary in the transcript; the factory's checks raised only two flags, both
  explained (Matt's keynote really is 13 min; Miles's topic overlaps Matt's words).
- Misheard names resolved by topic: "Mel Parry" = Paari, "Vepa" = Vaibhav, "Kassu Big" = Qazybek, "I'm solo" =
  Saurabh, "I am of niche" = Avneesh, "Sude" = Sujoy, "Fumi Lashie" = Funmilade.
- Descript's 429 "a job is already running" waits (12) were absorbed without failures.

## What went wrong, and why

1. **Funmilade's upload failed 6 times** (3 in the first run, 3 in the redo). Cause: a dot in the speaker's name in the
   extension-less media name. Retrying could never work; each attempt left an empty 6 s composition.
2. **Devang's start lost** to the room's muted Zoom mic. No other source on hand.
3. **The repo was left mid-rebase** by Claude's own `git pull --rebase -q` (quiet flag hid the conflict) until it was
   noticed at commit time.
4. **Approval friction**: "go" is not an explicit approval; it took a direct question to get one.

## Same bug elsewhere

- **The Conf42 factory had already fixed this exact bug on 2026-11-03** ("Sumeet L. Ram", rejected six times), in its
  own copy of `descript.py`. The fix never reached the live factory, whose descript/media/machine modules were copied
  from Conf42's. The real gap: a fix in one factory's shared module isn't carried to the other.

## Numbers

- Recordings: r1 10:08:14 + r2 10:07:34 = 20.3 h.
- Lineup 26: 23 cut (14 main room, 9 meeting room), 3 no-shows (Jyotirmoy Sundi, Pujitha Paladugu - to confirm,
  Sirisha Ayyagari).
- Run 1: ~23:55-01:19 (~85 min) for 23 talks, 2 at a time; 12 Descript 429 waits; 1 talk failed.
- Descript credits: 255 in all (7.7-15.5 per talk, from state.json).
- Leftovers to delete: 6 empty 6 s compositions (3x "Funmilade A. Lesi", 3x "Funmilade A. Lesi (redo 2)"). The real
  one is "Funmilade A. Lesi (redo 2)" at full length.

## Actions

Done now:

- `descript.import_file`: the media name always ends in the file's extension. `tests/smoke.py --no-whisper`: all good.
- README "Learned from a real day: LLMday San Francisco", skill (room bleed, Zoom m4a), MANUAL and MANUAL-CLAUDE
  Changelog.

Open:

| Action | Owner | Finding |
|---|---|---|
| Venue recording checklist (Dallas action 1): never mute the room mic in Zoom; phone backup per room | Marek / event team | 2 |
| Confirm Pujitha Paladugu was a no-show | Marek | numbers |
| When fixing descript.py / media.py / machine.py in one factory, diff it against the other and port the fix in the same job (add to both skills) | Claude | 1 |
| After 2 identical Descript import failures, stop retrying the same file and say so (each retry leaves a leftover) | Claude, proposal first | 1 |
| Pull with a visible result (no `-q`) and check `git status` before starting a factory job | Claude | 3 |
