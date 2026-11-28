# Set up the LIVE talk factory on this computer - for Claude

The person said something like *"Set up the LIVE talk factory on this computer: clone https://github.com/sreday/sreday
into Documents/GitHub and follow live-factory/SETUP.md"*. Do all of it yourself, in this order, without asking them
anything except where a step says so. They may not be technical: say in one short line what you are doing at each
step, nothing more. Never ask them for a token, a key or a password - the sign-ins come from their own Drive.

## 1. The tools the computer needs

- **Git:** `git --version`. Missing:
  - Windows: `winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements`;
  - Mac: `xcode-select --install` (a system window asks them to click Install - tell them to).
- **Python 3.9+:** `python3 --version` (Windows: `py -3 --version`). Missing:
  - Windows: `winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements`;
  - Mac: the Command Line Tools above bring python3; else `brew install python`.
- On Windows a fresh install may not be on PATH in this shell yet: use the full path
  (`%LOCALAPPDATA%\Programs\Python\Python312\python.exe`, `C:\Program Files\Git\cmd\git.exe`).

## 2. The repos, side by side

In `~/Documents/GitHub` (make it if missing), clone what isn't there yet and `git pull` what is:

```
git clone https://github.com/sreday/sreday.git
git clone https://github.com/sreday/llmday.git
git clone https://github.com/sreday/platformday.git
git clone https://github.com/Prompt-Engineering-Conference/2026.promptengineering.rocks.git
```

All four are public, so no GitHub sign-in is needed to clone them. Not `devopsnotdead`: that brand is dead.

## 3. The factory itself

`python3 sreday/live-factory/bootstrap.py` (Windows: `py -3 sreday\live-factory\bootstrap.py`). It asks nothing.
It sets up:
- the Python environment and its packages;
- the CUDA libraries, on an NVIDIA PC;
- ffmpeg;
- this computer's settings file;
- then it runs the health check.

It takes 5-15 minutes. Run it in the background and wait for it to finish.

## 4. The sign-ins, from their Drive (no keys to type)

- Use the **Google Drive connector**: search `title = 'live-factory vault.json'`. It sits in their owner-only folder
  "Claude (private)".
- **No Drive tools in this session, or the search finds nothing:** this is the one thing to ask them. *"Please turn
  on the Google Drive connector for Claude (claude.ai > Settings > Connectors > Google Drive), signed in as
  mark@sreday.com, then tell me 'done'."* Then search again.
- **Install it:**
  1. `download_file_content` the vault.
  2. Write the base64 it returns, exactly as it is, to `~/.live-factory/vault.b64`.
  3. Run `env/bin/python -m live_factory vault install ~/.live-factory/vault.b64` from `sreday/live-factory`
     (Windows: `env\Scripts\python`). That deletes the file.
- The tokens pass through your context here. Never echo, quote or summarise them, and never write them anywhere
  else.

## 5. Check and hand over

- `env/bin/python -m live_factory doctor` (Windows: `env\Scripts\python`). Everything required must say OK:
  - the Descript token "works";
  - "YouTube sign-ins" lists sreday, llmday and platformday;
  - "Claude Code (transcript proofread)" is OK.
- Anything marked FIX: fix it yourself if you can (install it, run bootstrap again). Otherwise tell them in one plain
  sentence what is missing.
- Open the progress page: start `env/bin/python -m live_factory serve` in the background. It opens
  http://localhost:8043 in their browser.
- Tell them: *"Ready. To ship an event, start Claude Code in Documents/GitHub/sreday and say: please ship the talks
  from <event> through the live talk factory - and give me the recordings."*
- From then on, for any factory job, read `sreday/.claude/skills/live-factory/SKILL.md` first (in a new session
  started in the sreday folder it loads by itself).
