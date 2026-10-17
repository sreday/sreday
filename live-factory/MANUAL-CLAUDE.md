# LIVE talk factory - with Claude <span class="tag-good">Recommended!</span>

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

## 2. Process an event - what to say

Replace the event and paths with yours. Events are named like `sreday-2026-london-q1` or
`platformday-2026-austin-q4`; Claude can find the right one if you just say "PLATFORMday Austin Q4".

| Step | Say to Claude | What happens |
|---|---|---|
| Add | *"Add the recordings from /Volumes/CARD/DCIM for PLATFORMday Austin 2026 Q4, day 1."* (more rooms: *"…room 2 is track 2"*) | The files are copied in as a checked, read-only backup. Your originals are never touched. |
| Analyse | *"Analyse them."* | Sound, transcript, stills - free, on your computer. About 35 min for an 8-hour day; Claude runs it in the background and tells you when it's done. |
| Find the talks | *"Match the lineup."* | Claude reads the whole day: greetings, "I'm …", the MC, closing lines, Q&A, pauses, titles and abstracts. It gives you a table of every talk with start, end and anything doubtful, and asks you what only someone who was there knows ("who spoke last?"). |
| ✋ Approve | Open the workbench (http://localhost:8043), check the flagged cuts, tick them, **Save cuts** - or tell Claude *"approve all"* if you trust the table. | Nothing goes to Descript before this. |
| Descript | *"Run the factory."* | Claude says how many talks and roughly how many Descript credits (8-30 per talk), then cuts, uploads and edits them. Watch it on http://localhost:8043/status or ask *"how is it going?"*. |
| ✋ Review | Watch each talk in Descript, trim the extra footage (30 s before, 60 s after), tick **Reviewed** in the workbench - or tell Claude *"Thomas, Jay and MJ are reviewed"*. | Only reviewed talks are exported. |
| Export | *"Export the reviewed talks."* | Final light MP4s, subtitles, chapters, YouTube loudness. |
| YouTube | *"Write the YouTube items."* | Titles, descriptions and thumbnail links, ready to paste. You upload. |

## 3. Handy things to ask

- *"What's the status of PLATFORMday Austin?"* / *"Which talks are waiting for my review?"*
- *"Why is Daniel flagged?"* - Claude reads the flags and the transcript and explains.
- *"Which talks weren't recorded?"*
- *"Thomas's end is cut off - redo it with 2 more minutes."* - Claude fixes the cut and flags the talk for a redo: it
  is cut again and uploaded as a fresh composition "Thomas Cameron (redo 2)"; the old one stays in Descript.
- *"Edits are paused - what happened?"* - Claude reads the edit reports and tells you what to check in Descript
  before it resumes.
- *"Publish the status."* - updates the Factory logs tab of this page.

## 4. What Claude won't do on its own

- Approve cuts or mark talks reviewed - unless you tell it to.
- Touch, move or delete your recordings, or free disk space by deleting things - it tells you what to free.
- Handle your Descript token or any password.
- Upload to YouTube.

If something looks off, just describe it - *"the sound on Raul's talk is only on the left"* - and Claude finds out why.

---

## Changelog

- **2026-10-16** - Tabs renamed and reordered: **Run with Claude** (first), **No Claude, manual**, **Factory logs**.
- **2026-10-16** - First version, with the live-factory skill in the sreday repo.
