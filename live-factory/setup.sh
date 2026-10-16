#!/usr/bin/env bash
# LIVE talk factory - one-time setup on macOS / Linux:  bash live-factory/setup.sh
# Creates live-factory/env with the Python packages (faster-whisper included), installs ffmpeg if it is missing,
# asks for the Descript token once (a machine set up for the Conf42 factory already has it), puts a
# "LIVE talk factory" launcher on the Desktop, checks the machine, then opens the dashboard.
# LF_NO_LAUNCH=1 skips opening the dashboard at the end.
set -e
cd "$(dirname "$0")"
export PYTHONUTF8=1
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v $c >/dev/null && $c -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" 2>/dev/null; then PY=$c; break; fi
done
[ -n "$PY" ] || { echo "Install Python 3.9+ first (brew install python)."; exit 1; }
[ -x env/bin/python ] || { echo "Creating the Python environment with $($PY --version) ..."; $PY -m venv env; }
echo "Installing Python packages (faster-whisper is ~100 MB) ..."
env/bin/python -m pip install -q --upgrade pip
env/bin/python -m pip install -q -r requirements.txt
if ! env/bin/python -c "from live_factory import machine;import sys;sys.exit(0 if machine.find_tool('ffmpeg') else 1)"; then
  if [ "$(uname)" = "Darwin" ]; then brew install ffmpeg; else sudo apt-get install -y ffmpeg; fi
fi
chmod +x run.sh setup.sh
env/bin/python -m live_factory setup || { echo; echo "Fix the items marked FIX above, then run setup.sh again."; exit 1; }
if [ -z "$LF_NO_LAUNCH" ]; then echo; echo "All set. Opening the dashboard ..."; exec ./run.sh; fi
