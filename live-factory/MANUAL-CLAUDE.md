# LIVE talk factory - with Claude

<p class="tag-good">Recommended!</p>

The same job as the **No Claude, manual** tab, but you talk to Claude and it runs the factory for you: it adds the recordings,
analyses them, reads the whole day's transcript to find every talk, cuts them, sends them to Descript, exports and
writes the YouTube texts. You only do the two checks a person has to do - approving the cuts and reviewing the talks
in Descript - and the YouTube upload.

Claude knows the factory through the **live-factory skill** that comes with the sreday repo, so any teammate's Claude
works the same way.

---

## 1. One-time setup

1. **Claude Code**: install it from https://claude.com/claude-code (the desktop app, or in a terminal
   `npm install -g @anthropic-ai/claude-code`) and sign in with your Claude account.
2. **The repos**, side by side in one folder (e.g. `Documents/GitHub/`):

    ```
    git clone https://github.com/sreday/sreday.git
    git clone https://github.com/sreday/llmday.git        # for LLMday events
    git clone https://github.com/sreday/platformday.git   # for PLATFORMday events
    ```

3. **The factory itself**, once - this one you run yourself, because it asks for your Descript API token
   (Descript → Settings → API tokens), which should never go through a chat:

    - Mac: `bash sreday/live-factory/setup.sh`
    - Windows: double-click `sreday\live-factory\setup.cmd`

4. **Start Claude in the sreday folder**: open a terminal there and run `claude` (or open the folder in the desktop
   app). Check it with: *"Is the LIVE talk factory ready on this computer?"* - Claude runs the health check and tells
   you what, if anything, is missing.

---

## 2. Ship an event in one go (autopilot)

Say *"Please ship the talks from SREday London 2026 Q1 through the live talk factory"* and give Claude the
recordings (and the separate sound, if any). Claude does everything else without asking:
- it finds the talks and their sharp starts and ends;
- it checks the slides and the sound;
- it approves the cuts itself;
- it runs Descript;
- after the export it checks every finished video itself, in place of your review in Descript: its first and last
  words heard again, no silent stretch, no black picture, loudness, length;
- it uploads each checked talk to the brand's YouTube channel.

On YouTube, each talk gets:
- its title, description and chapters;
- the website's thumbnail and the subtitles;
- a place in the event's playlist, in the order of the schedule;
- a schedule: **private, public exactly 5 weeks after the event at 09:00 local time**.

Claude also puts each link into the event's sheet (the website shows the video only from that day). Then it writes
`ship report.md` and a postmortem:
- what is on YouTube and when it goes public;
- which talks are missing and why;
- which are problematic and what would fix them;
- the Descript credits, and the leftover compositions for you to delete.

Afterwards, just say what you want: *"fetch Kevin's talk from this file"*, *"redo Jay's talk"*. A talk that isn't sure
(its start lost, its sound dying) is never guessed: it waits, and the report says why.

### A new computer in one sentence

Install Claude Code (claude.com/claude-code) and sign in with your account. Open it anywhere and paste this, word for
word:

> Set up the LIVE talk factory on this computer: clone https://github.com/sreday/sreday into Documents/GitHub and
> follow live-factory/SETUP.md

That's all. Claude installs whatever is missing (Git, Python, ffmpeg), clones the brand repos, installs the factory,
signs it in from your Drive, checks it and opens the progress page. The only thing it may ask: to turn on the Google
Drive connector (claude.ai > Settings > Connectors). Later, on a computer that already has the repo, you can also
start Claude Code in the sreday folder and say *"set up the factory on this computer"*. Claude
clones the other brand repos, installs everything (`live-factory/bootstrap.py` - no questions), fetches your
sign-ins from your private Drive vault through your Claude account's Drive connector (the YouTube channels and the
Descript token - no API key to type), and runs the health check. Needed on the computer: Git, Python 3.9+, Claude Code
signed in with your account (with the Google Drive connector on).

The vault is one file, `live-factory vault.json`, in your owner-only Drive folder "Claude (private)". Claude keeps it
current whenever a sign-in changes. To revoke everything at once: Google account > Security > third-party access
("Conf42 factory"), and Descript > Settings > API tokens.

### Launch checklist: once per computer (by hand, or what Claude checks)

Do these once on every computer that ships events, Mac or Windows. Ask Claude *"Is the factory ready to ship
<event>?"*: it runs the checks and tells you what is missing. A run that starts without them stops at the first
missing piece.

1. **The repos, side by side in one folder** (e.g. `Documents/GitHub/`), all up to date with `git pull`. The
   factory is in the first one; each event's lineup comes from its own brand's repo.
    ```
    git clone https://github.com/sreday/sreday.git
    git clone https://github.com/sreday/llmday.git
    git clone https://github.com/sreday/platformday.git
    git clone https://github.com/Prompt-Engineering-Conference/2026.promptengineering.rocks.git
    ```
2. **Python and ffmpeg.**
    - Mac: `brew install python ffmpeg`.
    - Windows: Python 3.12 from python.org (tick "Add to PATH"), and `winget install Gyan.FFmpeg`.
3. **The setup, by you** (it asks for your Descript API token, which never goes through the chat):
   `bash sreday/live-factory/setup.sh` on the Mac, or double-click `sreday\live-factory\setup.cmd` on Windows.
    - **Run it again after every update that changes `requirements.txt`**; the 2026-11-27 update added `tzdata` and
      `pillow`. Otherwise `doctor` says "Python packages: missing".
4. **YouTube, once per channel, by you:**
    - You need to be the channel's **owner or manager**: an editor can't sign the factory in (PEC, for now).
    - You need the OAuth client file of the Google Cloud project "Conf42 factory" (a "Desktop app" client, JSON).
      Copy it to the computer yourself, from the private folder, e.g. to `~/.claude/private-work/youtube-client.json`.
      Never put it in a repo or a chat.
    - Then, in Claude Code (on Windows use `env\Scripts\python` in place of `env/bin/python`):
      ```
      ! cd sreday/live-factory && env/bin/python -m live_factory youtube-login sreday ~/.claude/private-work/youtube-client.json
      ```
      On Google's "Choose an account or brand account" screen, pick the **SREday channel itself**, not your own
      account. Past the "unverified app" warning, use *Advanced*. Then do the same for `llmday`, `platformday` and
      `pec`.
    - The sign-in stays on that computer only, in `~/.live-factory/youtube/`, outside every repo. A channel other than
      the brand's is refused.
5. **Claude Code on this computer, signed in** - it proofreads the subtitles before they go to YouTube (on your
   subscription, no API key). `doctor` shows "Claude Code (transcript proofread)".
6. **The health check:** *"run the factory doctor"*. Everything must say OK, and "YouTube sign-ins" must list the
   brands you ship.
7. **The first run downloads Whisper** (large-v3, about 3 GB, into `~/.live-factory/whisper`), so it needs the
   internet and the disk space.
8. **Disk:** 150 GB free for a full day: the copied recordings, the cuts, Descript's exports and the finals.
9. **Power:** plugged in, with the lid open. While a job runs the factory keeps the computer from sleeping by itself,
   but a closed laptop lid, a flat battery or an update restart still stop it. On Windows, pause Windows Update for
   the day.
10. **One folder per computer:** always run the factory from the same `sreday/live-factory`. Its `local/` folder keeps
   the ledger of paid Descript edits, which stops an edit being paid for twice.
11. **One computer per event:** the event's work folder, its state and its YouTube record all live on the computer
   that runs it.

How fast it goes:
- **A PC with an NVIDIA card** (Elephant, RTX 5090): an 8-hour day is analysed in about 15 minutes, and the edges
  take about 1 minute a talk.
- **A Mac (M1/M2):** the analysis takes about 35 minutes; the edges run on large-v3 too, a few minutes a talk.

The Descript part and the uploads take the same time on either.

### Launch checklist: before each event

1. `git pull` the brand's repo. The event's `metadata.yml` must have:
    - `start_time` with the UTC offset, e.g. `"2026-10-23T09:00:00-05:00"` - the release date comes from it;
    - `city_name`, whose time zone must be in the factory's `settings.yml` (`youtube.timezones`) - else the release
      can be an hour off in summer time; Claude adds a missing city;
    - the brand's `youtube_url`.
2. **The thumbnails exist:** open `https://<brand>.com/<event>/teasers/` - the YouTube tab must show a picture for
   every talk. The website draws them; a talk added late needs the site rebuilt first.
3. **The decks** came in through the deck dump (`/deckdump`). Claude also checks this and lists the missing ones.
4. **The recordings**, and any separate sound, are on this computer or on a disk plugged into it.
5. Then say *"Please ship the talks from <event> through the live talk factory"* and give the paths.

Things to know during a run:
- **The YouTube limit:** about 100 uploads a day per Google project. A big event or two events in one day can hit
  it; the run says so, and the next run (or *"upload <event>"*) carries on.
- **Leftovers in Descript:** a failed upload can leave a composition behind. The API can't delete one, so they are
  listed in the report for you to delete.
- **The report and the postmortem** come at the end. Talks that are missing, problematic or held are named there,
  each with why.

## 3. Process an event step by step - what to say

Replace the event and paths with yours. Events are named like `sreday-2026-london-q1` or
`platformday-2026-austin-q4`; Claude can find the right one if you just say "PLATFORMday Austin Q4".

| Step | Say to Claude | What happens |
|---|---|---|
| Launch | *"Launch the factory."* | Claude starts the app if needed and opens the progress page (http://localhost:8043) in your browser: bars per stage and per talk, then only what needs you. The detailed workbench is http://localhost:8043/workbench. |
| Add | *"Add the recordings from /Volumes/CARD/DCIM for PLATFORMday Austin 2026 Q4, day 1."* (more rooms: *"…room 2 is track 2"*) | The files are copied in as a checked, read-only backup. Your originals are never touched. |
| Separate sound | *"The sound was recorded on the desk too: D:\ZOOM0001.wav, day 1."* | Added as separate sound. After the analysis it is placed on the videos by itself (different start, clock drift, files split in pieces - all fine); the talks it covers get its sound. |
| Analyse | *"Analyse them."* | Sound, transcript, stills - free, on your computer. About 35 min for an 8-hour day; Claude runs it in the background and tells you when it's done. |
| Find the talks | *"Match the lineup."* | Claude reads the whole day: greetings, "I'm …", the MC, closing lines, Q&A, pauses, titles and abstracts. It gives you a table of every talk with start, end and anything doubtful, and asks you what only someone who was there knows ("who spoke last?"). |
| Sharp ends | *"Check the starts and ends."* (Claude does it anyway before the run) | The factory listens closely at both ends of every talk; Claude reads each one and gives you its first and last sentence: the speaker's first words to the room, their last finished sentence before the Q&A (ideally the closing "thank you"). The Q&A is always out; a talk never stops mid-sentence. Anything unsure, Claude asks you. |
| Saturated sound | *"Which talks have saturated sound?"* | The talks whose sound is clipped (cracking on loud words), with the minutes where. In the run: the clean track where there is one; else the repair tools tried on 30 s and judged, the best one repairs the talk (normal Studio Sound); Studio Sound 100% only when none helps. |
| Slides check | *"Look at the slides."* | For every cut: which deck pages it found on screen and will show full screen, build steps, night mode, the separate sound - with a before/after picture per talk. Claude names any deck that looks like another version than the one presented: if only the fonts differ it can still use it (*"use Kevin's deck anyway"*); if the words differ, ask the speaker for the deck they presented. |
| ✋ Approve | Open the workbench (http://localhost:8043/workbench), check the flagged cuts, tick them, **Save cuts** - or tell Claude *"approve all"* if you trust the table. | Nothing goes to Descript before this. |
| Descript | *"Run the factory."* | Claude says how many talks and roughly how many Descript credits (8-30 per talk), then cuts, uploads and edits them. Watch it on http://localhost:8043/status or ask *"how is it going?"*. |
| ✋ Review | Watch each talk in Descript (the start and the end are final - nothing to trim), tick **Reviewed** in the workbench - or tell Claude *"Thomas, Jay and MJ are reviewed"*. | Only reviewed talks are exported. |
| Export | *"Export the reviewed talks."* | Final light MP4s, subtitles, chapters, YouTube loudness. |
| YouTube | *"Write the YouTube items."* | Titles, descriptions and thumbnail links, ready to paste. You upload. |

## 4. Handy things to ask

- *"What's the status of PLATFORMday Austin?"* / *"Which talks are waiting for my review?"*
- *"Why is Daniel flagged?"* - Claude reads the flags and the transcript and explains.
- *"Which talks weren't recorded?"*
- *"Thomas's end is cut off - redo it with 2 more minutes."* - Claude fixes the cut and flags the talk for a redo: it
  is cut again and uploaded as a fresh composition "Thomas Cameron (redo 2)"; the old one stays in Descript.
- *"Edits are paused - what happened?"* - Claude reads the edit reports and tells you what to check in Descript
  before it resumes.
- *"Publish the status."* - updates the Factory logs tab of this page.
- *"Which compositions do I delete in Descript?"* - Claude lists the leftovers failed uploads left behind (same name
  as the talk, so it gives the length of the one to delete).

## 5. What Claude won't do on its own

- Approve cuts or mark talks reviewed - unless you tell it to, or you ask it to ship an event (autopilot, above).
- Touch, move or delete your recordings, or free disk space by deleting things on its own. When your disk is low it offers to clean up junk and cache
  files: it shows you what they are and how big, and deletes only what you pick.
- Handle your Descript token or any password.
- Upload to YouTube - except when you ask it to ship an event, and then only to the brand's own channel, private
  until the release.

If something looks off, just describe it - *"the sound on Raul's talk is only on the left"* - and Claude finds out why.

---

## Changelog

- **2026-11-28** - A new computer in one sentence: *"set up the factory on this computer"*. Claude installs
  everything with no questions and brings the YouTube sign-ins and the Descript token from your private Drive vault,
  so no per-computer keys or sign-ins.
- **2026-11-27** - The app opens on a plain progress page (bars, then only the real issues); the workbench is at
  /workbench. The subtitles are proofread by Claude in the talk's context before they go to YouTube. After a full
  review of the factory with Claude Fable, about 40 problems were fixed, among them:
  - no Descript edit paid for twice after a network blip;
  - a damaged recording no longer hangs the cut;
  - portrait phone videos are no longer squashed;
  - a talk from two cameras of different sizes is joined properly;
  - a talk with no sound stops before Descript;
  - the Windows crash after about 100 Whisper uses is gone;
  - the speaker's own "on stage" / "next up" is no longer taken for the MC;
  - one job per event at a time.
- **2026-11-27** - Launch checklist for shipping a whole conference on any Mac or Windows computer (section 2). The
  factory now keeps the computer awake while a job runs. The upload got tougher too: a talk YouTube refuses no
  longer stops the others, and a rerun uploads what the day's limit left.
- **2026-11-27** - Autopilot: *"ship the talks from <event> through the live talk factory"* runs the whole way to
  the brand's YouTube channel. Claude approves the cuts, the factory checks each final video itself, and the upload
  carries the title, description, chapters, thumbnail, subtitles and playlist. Each talk goes up private and becomes
  public 5 weeks after the event at 09:00 local. Then come the sheet links, a ship report and the postmortem.
- **2026-11-27** - Sharp starts and ends: the factory sets every talk's first and last words itself (Q&A always out,
  never mid-sentence, a close that trails off ends on the firm sentence before it); no extra footage to trim in
  Descript.
- **2026-11-27** - Saturated sound: clipped talks are found (also before the run: *"which talks have saturated
  sound?"*); the clean track is used where there is one, else the best repair tool that proves itself on a sample;
  Studio Sound 100% in Descript only as the last resort.

- **2026-11-27** - Every cut is checked before Descript (pages in the picture, the recorder's sound in sync, the
  camera's pilot sound gone); a failing talk stops before any credits are spent and Claude tells you why.
- **2026-11-27** - Best slides and best sound: deck pages replace the slides on screen (Presenter View, taskbar,
  headshot, a TV in the room), builds kept, demos stay video; night mode put right; sound recorded on its own device
  is placed on the videos and used. Ask *"look at the slides"* before a run to see it per talk.

- **2026-11-25** - Fixed: a speaker with a dot in their name (e.g. "Funmilade A. Lesi") no longer fails at the Descript
  upload. Claude also watches for an emptied room picking up sound from the other room, and knows the m4a in a Zoom
  Drive zip can't fill a muted stretch.

- **2026-11-24** - Talks that arrive already edited (one file per talk, from a partner) go to Descript whole: no trim,
  their Q&A kept. Fixed: a talk with only a short title and no abstract no longer makes every other talk look like a
  "swapped slot".

- **2026-11-10** - Claude checks which room each recording shows (the Meet/Zoom tile name on the frames), because one
  recording can switch rooms during the day (PLATFORMday San Francisco: the keynotes, then the mini room).
- **2026-10-29** - Claude cuts every closing Q&A (you can put one back from the 60 s margin), asks before adding a
  talk that isn't in the lineup, sends talks it is unsure of to Descript when you say so, and tells you when a Zoom
  recording has stretches with the mic muted. Runs on Windows with an NVIDIA card. After every event Claude writes a postmortem
  (`live-factory/postmortems/`) and acts on what it finds.

- **2026-10-19** - Claude's morning report lists the leftover Descript compositions from the factory's own check
  (with lengths), and the factory checks disk space before every cut and export, not only at the start.

- **2026-10-17** - When the disk is low, Claude offers to clean up junk and cache files and shows you what first.
- **2026-10-17** - Each event gets its own Desktop folder named after the conference (`video factory - LLMday Austin
  2026 Q4`).
- **2026-10-17** - "Launch the factory" opens the workbench on the event you're working on.
- **2026-10-17** - "Launch the factory" now always opens the workbench in your browser. Claude also writes each new
  practice it learns on a real day into the factory's README and these manuals straight away.
- **2026-10-16** - Tabs renamed and reordered: **Run with Claude** (first), **No Claude, manual**, **Factory logs**.
- **2026-10-16** - First version, with the live-factory skill in the sreday repo.
