# LIVE talk factory - team manual

<p class="tag-slow">Probably slower than manual editing for non-tech people...</p>

Turns the long recordings of a live event (SREday, LLMday, PLATFORMday, PEC, DevOps Not Dead) into one clean,
YouTube-ready video per talk. Descript only gets the talks themselves: the breaks, set-up chatter, lunch and Q&A
never reach it, which saves about a third of its media hours.

```
recordings  →  analyse  →  find the talks  →  ✋ you check the cuts  →  cut + Descript edit  →  ✋ you check in Descript  →  export  →  YouTube
```

Two steps are yours (✋). Nothing is uploaded before you approve the cuts, and nothing is exported before you've
looked at each talk in Descript.

> This is the **No Claude, manual** tab. The **Factory logs** tab shows what the factory has done, live: every computer that runs it
> publishes its progress - at the end of every job, and during a run whenever a talk is ready for review or finished
> (at most every 20 minutes) - and the tab checks for news every minute. **Publish status** in the workbench publishes
> right away. The same page is on sreday.com/factory, llmday.com/factory and platformday.com/factory; each shows its
> own brand's events (tick "show the other brands" for all).
>
> This page is the manual for operating the tool. The technical details live in `live-factory/README.md` in the
> sreday repo. When the tool changes, this page changes with it - see **Changelog** at the bottom.

---

## 1. One-time setup on your computer

You need: a Mac or a Windows PC with **150 GB free** for a full day of recordings, Git, and Python 3.9 or newer
(the setup installs the rest).

1. **Get the repos.** Put them side by side in one folder, e.g. `Documents/GitHub/`:

    ```
    git clone https://github.com/sreday/sreday.git
    git clone https://github.com/sreday/llmday.git          # only if you process LLMday events
    git clone https://github.com/sreday/platformday.git     # only if you process PLATFORMday events
    ```

2. **Run the setup**, once:

    - **Mac:** `bash sreday/live-factory/setup.sh`
    - **Windows:** double-click `sreday\live-factory\setup.cmd`

    It installs ffmpeg and Whisper (the free speech-to-text that runs on your computer) and asks for a **Descript API
    token**: in Descript go to **Settings → API tokens → Create**. Use the team's Descript account. The token is stored
    on your computer only, never in git. It also puts a **LIVE talk factory** launcher on your Desktop.

3. **Check it:** the setup ends with a health check. Everything should say `OK`. You can run it again any time:
    `env/bin/python -m live_factory doctor` (Mac) or `env\Scripts\python -m live_factory doctor` (Windows), from the
    `live-factory` folder.

**Start the app:** double-click **LIVE talk factory** on your Desktop (or `./run.sh` / `run.cmd` in `live-factory`).
It opens two pages in your browser:

- **Workbench** - http://localhost:8043 - where you do everything.
- **Status** - http://localhost:8043/status - read-only progress of the whole day; leave it open in a tab.

The workbench opens on the event you worked on last (the events you've started are marked ● at the top of the list),
and the address shows it (`?event=llmday-2026-austin-q4`), so a bookmark or a reload keeps it. Finished events in a
repo's `past/` folder are listed too. Starting the app again while an older copy is still running replaces that copy;
jobs already running carry on.

Before every event, update the repos so the lineup is current: `git pull` in each repo folder.

---

## 2. Process an event, step by step

Events are named `<brand>-<folder>`, e.g. `sreday-2026-london-q1` or `platformday-2026-austin-q4` - the folder name
from the site repo. Pick the event in the dropdown at the top of the workbench.

### Step 1 - Add the recordings

In the workbench, open **1. Recordings → Add recordings**:

1. Paste the path of the recording, or of the folder on the SD card / drive that holds them, one per line.

    - Mac: drag the file into the Terminal to get its path, e.g. `/Volumes/CARD/DCIM/day1.mp4`.
    - Windows: Shift + right-click the file → **Copy as path**.
2. Pick the **Day**, and the **Track** (the room) when the day had more than one room. A one-room day needs only the day.
3. Leave **read in place** off: the recording is then copied into the factory's folder as a checked, read-only backup.
    Tick it only when the file is on a drive that stays plugged in and you don't want a second copy - then that file is
    the only copy, so back it up.

4. Press **Add**.

**Sound recorded on its own device** (a recorder on the mixing desk, the AV team's laptop - common with a wired set-up,
where the camera only has its built-in mic): add those files too, the same way, for the same day. Sound files (.wav,
.mp3, .m4a, .flac...) are taken as *separate sound* by themselves; a video file that should only give its sound goes in
with Claude's *"…it's sound only"* (`--sound`). After the analysis the factory finds where each piece of sound belongs
on the videos by itself - however the files start and stop, whatever their clocks say, even when one device drifts
against the other - and every talk it covers gets that sound instead of the camera's. Where the recorder has a hole,
was muted or is clearly worse, the camera's own sound stays.

The factory never changes, moves or deletes your original files. If the disk doesn't have room for the whole job
(about 2× the recordings), it refuses to start and tells you how much to free - with a red banner and a desktop
notification.

### Step 2 - Analyse

Press **Analyse recordings**. On your computer, for free, it:

- copies the recording in and checks the copy,
- measures the sound per second (and finds a recording that has sound on one side only),
- writes a transcript with Whisper,
- takes stills,
- places every separate sound recording on the videos (an 8-hour day takes about a minute).

An 8-hour day takes about **35 minutes** on an M1/M2 Mac (faster on a PC with an NVIDIA card). You can watch it on
the Status page and close the browser - it keeps running.

### Step 3 - Find the talks

Two ways:

- **Automatic:** press **Draft cuts**. The factory matches the recording to the lineup: the speaker's "Hi, I'm Dan",
    the MC's "please welcome…", greetings, the closing "thank you", the Q&A, the pauses. On our first real day it got
    13 of 15 talks within 30 seconds and spotted the no-show.

- **With Claude Code** (best results): press **Brief for Claude**, then ask Claude: *"match the lineup for
    platformday-2026-austin-q4"*. Claude reads the whole transcript, checks frames at every boundary, and writes the cuts.

Either way, the result is a list of cuts, one per talk, waiting for you.

### Step 4 - ✋ Check and approve the cuts (gate 1)

Section **2. Cuts** in the workbench, one tab per track:

- The **player** shows the recording; the **waveform** under it shows the left channel above the line and the right
    channel below (a one-sided recording shows only half). Click the waveform to jump there. Cuts are shaded - green
    once approved.

- For every cut:

    - **▶ start** plays from the cut's start, **▶ end** plays its last 15 seconds.
    - **−5 −1 +1 +5** nudge a time by seconds; **⇤ / ⇥** set the start / end to where the player is now.
    - **words** shows what is said around the start and the end.
    - **+ part** - when a talk continues into the next recording file.
    - The talk's name can be changed with the dropdown (a swapped slot).
- **Read the flags (⚑, red)** - they are why you're here: very quiet or distorted sound, sound dropping out, too few
    words, the picture blank, much shorter or longer than its slot, or *"sounds like X's talk - swapped slot?"*.

- Under the list: **Not cut** (not recorded properly, or a no-show) and talks **neither cut nor skipped**. Check these
    with whoever ran the room.

- Tick the checkbox of every cut you're happy with. **Approve all unflagged** ticks the clean ones; flagged and
    low-confidence cuts must be ticked one by one, on purpose.

- Press **Save cuts**.

You don't need to be precise to the second: every clip gets **30 seconds of extra footage before and 60 after**, so
you can trim it exactly in Descript. A cut must not *miss* part of the talk, though.

### Step 5 - Cut and edit in Descript

Press **Run**. For every approved talk the factory:

1. cuts it out of the recording (fixing sound that is on one side only, and zooming in slides that sit in black bars),
    and makes the picture and the sound the best they can be:
    - **the slides, sharp and full screen**: wherever the recording shows a page of the speaker's deck - inside
      PowerPoint's Presenter View, with the taskbar or a headshot over it, or filmed on a small TV in the room - that
      stretch shows the deck page itself instead. Bullets that appear one by one appear one by one. A live demo, a
      browser, code or the speaker on stage keep the recording. The deck comes from the deck dump (the talk page's
      "Download slides" PDF) or from a PDF you drop into the event folder's `decks/` named after the speaker;
    - **night mode** (a yellow, Night Shift screen share): the deck pages have the true colours anyway; what stays
      video gets its colours put back;
    - **the separate sound** where there is one (step 1).

    **Then the factory checks the cut itself before anything is uploaded** (Descript costs money): the deck pages
    really are in the picture, the separate sound really is in the cut, in sync, and the camera's own "pilot" sound
    is gone. A talk that fails stops there - no credits spent - and says what is wrong; `cuts/<speaker>.check.jpg`
    shows frames of the cut.

    Before the run you can see what it will do: ask Claude *"look at the slides for PLATFORMday Austin"* - each talk
    gets a before/after picture `cuts/<speaker>.picture.jpg` in the event folder.
2. uploads it to the event's Descript project (e.g. **PLATFORMday Austin 2026 Q4**), named after the speaker,
3. has Descript's AI editor apply **Studio Sound 80%**, a limiter, the volume, remove the **um / uh** sounds (only
    those - real words are kept) and shorten pauses longer than 2 s to 1 s.

Then it stops and waits for you. Two talks go at a time; a day of 15 talks takes about an hour. Each edit costs about
8-30 Descript AI credits - the Status page shows the total.

### Step 6 - ✋ Review in Descript (gate 2)

Open the event's project in Descript (link on the Status page). For every talk:

1. **Trim the extra footage**: the workbench shows where the talk is inside the clip - *"talk runs 0:30 → 25:12 in the
    clip"*. Remove what is before the talk and after it (the MC, the Q&A, the next set-up).

2. Watch / skim it: the sound, the start and the end, the cuts around removed filler sounds.
3. Back in the workbench, section **3. Talks in Descript**, tick **Reviewed**.

**Something wrong with a talk** (cut off, wrong talk, bad zoom)? Fix its cut in section 2 if needed and save, then in
section 3 type what's wrong and press **flag for redo**. On the next Run it is cut again and uploaded as a fresh
composition, e.g. **"Thomas Cameron (redo 2)"** - the old one stays in Descript untouched, so you can compare.

### Step 7 - Export

Press **Run** again. Every talk ticked **Reviewed** is:
published from Descript → downloaded → subtitles (SRT) → YouTube chapters → trimmed to the first and last word →
set to YouTube loudness (-14 LUFS) → saved as a light MP4 → checked.

The finished videos are in the event's folder, `Desktop/video factory - <event>/final videos for YouTube/`, named
`<speaker> - <Event>.mp4`. The event folder (button **Event folder**) has `YouTube items.txt`.

### Step 8 - YouTube

For each talk, `YouTube items.txt` has, ready to paste:

- the **title**: `<talk title> | <speaker> | SREday London 2026 Q1` (at most 100 characters),
- the **description** in the channel's usual format (event and date, tickets link, CFPs link, chapters),
- the **thumbnail** link - the website already draws it; download and upload it.

Upload to the brand's channel as usual. Then put the YouTube link in the event's sheet (the **YouTube** column) - in
the Google Sheet, never in the repo's CSV.

---

## 3. When something goes wrong

| What you see | What to do |
|---|---|
| Red banner **Not enough disk space** | Free the space it names, or set `work_root: D:/live-factory` (a bigger drive) in `live-factory/settings.local.yml`. Nothing was started, or the talk named in the banner stopped before writing its file - run again and it carries on. |
| Banner **Delete in Descript - leftovers of failed uploads** | A failed upload left a composition with the talk's name - an empty 6 s one, or an unedited full-length twin. Delete the one with the length shown; the real, edited talk is the other one. The factory can't delete it itself (Descript doesn't allow it). |
| A talk is **failed** at upload | Press **retry** next to it. A dropped connection is retried 3 times automatically first. |
| **EDITS PAUSED** in the log | The factory stopped all edits because a report looked like the AI editor touched another talk, or one edit cost too much. Check that talk in Descript; if all is fine, clear the pause - `env/bin/python -m live_factory unpause <event>` in the `live-factory` folder (Windows: `env\Scripts\python`) - then Run. |
| Descript says **402** | The Descript plan is out of credits or media minutes. Top it up, then Run - finished steps are never paid for twice. |
| A talk's sound is on one side only | Nothing - the factory fixes it (it copies the live side to both). |
| The wrong talk got a name, or a talk is missing | Fix it in step 4 (change the name, add a cut with **+ cut at the playhead**), or flag it for redo in step 6. |
| A talk **wasn't recorded** | It shows under **Not cut**. Tell the speaker; there is nothing to process. |
| The workbench doesn't open | Double-click the launcher again; if the page is still blank, run `doctor` (step 1.3). |
| The run and the workbench **stopped with no error** | Something outside the factory closed them (it happened once in Dallas). Open the workbench again and press **Run**: it carries on where it stopped, and an edit Descript already finished is not paid for again. |
| A stretch of a talk has **no sound at all** | In a Zoom recording, the room mic was muted in Zoom. The file can't bring it back - look for another recording (a phone, a camera, the Zoom cloud recording). |
| You started a job and closed the browser | That's fine - jobs run in the background. Reopen the workbench to see them. |

**One computer per event.** Two people processing the same event at once would fight over the Descript project.
Agree who runs which event.

---

## 4. Where things are

On the computer that runs the event, one folder per conference on the Desktop, e.g.
`Desktop/video factory - LLMday Austin 2026 Q4/`, so several events can run side by side:

| Folder / file | What's in it |
|---|---|
| `in/` | the backup copies of the recordings (read-only - never edit or delete) |
| `rec/` | the analysis: transcripts, sound levels, stills |
| `cuts/` | the talks as cut and uploaded to Descript |
| `out/`, `srt/` | Descript's downloads, subtitles |
| `cuts.json` | the cuts you approved (step 4) |
| `YouTube items.txt` | titles, descriptions, thumbnails (step 8) |
| `final videos for YouTube/` | the finished videos of this event |

When an event is published on YouTube and the raw recording is backed up elsewhere, the event folder can be archived
or deleted to free space - ask first, the `in/` copy may be the only backup of the recording.

---

## Changelog

- **2026-11-27** - A check of every cut before it goes to Descript: deck pages in the picture, the separate sound in
  sync and the camera's sound suppressed - a cut that fails is stopped before any credits are spent.
- **2026-11-27** - The slides, sharp and full screen: wherever a talk's recording shows a page of the speaker's deck
  (Presenter View, the taskbar or a headshot over it, a TV filmed in the room), the video shows the deck page itself;
  builds appear as they did, demos keep the recording. Night mode (a yellow screen share) is put right. Sound recorded
  on its own device is placed on the videos by itself and used for the talks it covers. A deck that is a different
  version from the one presented is left alone (the recording stays) and named in the notes.

- **2026-11-25** - Fixed: a talk whose speaker has a dot in their name (e.g. "Funmilade A. Lesi") was refused by
  Descript as "invalid or unsupported media" on every try. It now uploads like any other.

- **2026-11-24** - Talks that arrive already edited (one file per talk, from a partner) go to Descript whole: no trim,
  their Q&A kept. Fixed: a talk with only a short title and no abstract no longer makes every other talk look like a
  "swapped slot".

- **2026-11-10** - Fixed: an event moved to `past/` could be missing from the Factory logs tab when a local site
  build had left a folder behind at the old place. `<command> --help` shows the help.
- **2026-10-29** - Works on Windows with an NVIDIA card (first day: SREday Dallas 2026 Q4 on an RTX 5090, a whole
  8.7 h day transcribed in about 15 min). Fixed: on Windows the run could stop with "Access is denied" while the
  workbench was open, and ticking Reviewed during a run could do the same. Fixed: transcribing failed with a newer PyAV (`metadata_errors`).
- **2026-10-29** - Closing Q&As are always cut; the 60 s after the talk is there if you want a question back.

- **2026-10-19** - Disk space is checked again before each talk is cut and before Descript's export is downloaded,
  not only at the start of a run. A full disk stops that talk with the red banner, and the next run carries on.
- **2026-10-19** - After each run the workbench lists the leftover compositions failed uploads left in Descript
  (empty 6 s ones, or an unedited full-length twin), with their length, so you know which one to delete.
- **2026-10-19** - A talk far quieter than the rest of its recording (a mic left far away) is flagged again, even
  when the transcript understands it.

- **2026-10-17** - One folder per event on the Desktop, named after the conference: `video factory - LLMday Austin
  2026 Q4`, with its own `final videos for YouTube/`. Opening an event in the app no longer makes a folder for it.
- **2026-10-17** - Fewer false sound flags: when the room is nearly as loud as the speaker, stretches the
  transcript hears as speech are no longer flagged as drop-outs or "hardly any sound".
- **2026-10-17** - The workbench opens on the event you worked on last (or `?event=` in the address), lists events
  that have moved to `past/`, and an app left running from an older version is replaced when you start it again.
- **2026-10-16** - Tabs renamed and reordered: **Run with Claude** (first), **No Claude, manual**, **Factory logs**.
- **2026-10-16** - New **With Claude** tab: run the factory by talking to Claude Code (the live-factory skill comes
  with the sreday repo).
- **2026-10-16** - The factory pages ask for the team password once per device (ask Mark).
- **2026-10-16** - The page is on sreday.com, llmday.com and platformday.com (/factory), each with its own brand's
  events; the Factory tab updates live, every minute.
- **2026-10-16** - Two tabs on this page: **Manual** and **Factory**, where runs publish their progress (talk names,
  steps and results only). **Publish status** button in the workbench, `unpause` command for paused edits.
- **2026-10-16** - Every clip gets 30 s of extra footage before the talk and 60 s after, for trimming in Descript.
    Slides boxed in black bars are zoomed to fit the frame. **Flag for redo** for any talk. The filler-word removal takes
    only um / uh sounds (it had muted real words). First real day processed: PLATFORMday Austin 2026 Q4.

- **2026-10-15** - First version.
