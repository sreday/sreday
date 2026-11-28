"""LIVE talk factory - set up on this computer with one command and no questions, so Claude can run it (Marek
2026-11-28: "I really need this flow: copy repo, install plugins, run this puppy - as long as my claude is used").

    python3 live-factory/bootstrap.py        (Windows: py -3 live-factory\\bootstrap.py)

Standard library only. It makes live-factory/env (Python packages; on an NVIDIA PC also the CUDA libraries Whisper
uses), installs ffmpeg when it is missing (winget on Windows, Homebrew on the Mac), writes settings.local.yml with this
computer's name, and runs the health check. It never asks for a token: the sign-ins (YouTube channels, Descript) come
from the person's private Drive vault, which Claude installs next (`python -m live_factory vault install <file>`, see
the live-factory skill). setup.sh / setup.cmd stay for a person who sets up by hand.
"""
import os
import shutil
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WIN = os.name == "nt"
PY = os.path.join(HERE, "env", "Scripts" if WIN else "bin", "python.exe" if WIN else "python")


def step(msg):
    print("\n== " + msg, flush=True)


def sh(args, **kw):
    print("   $ " + " ".join(args), flush=True)
    return subprocess.run(args, cwd=HERE, **kw).returncode


def main():
    if sys.version_info < (3, 9):
        raise SystemExit("Python 3.9 or newer is needed (Mac: brew install python; Windows: "
                         "winget install -e --id Python.Python.3.12)")
    env = dict(os.environ, PYTHONUTF8="1")
    if not os.path.exists(PY):
        step("the Python environment (live-factory/env)")
        if sh([sys.executable, "-m", "venv", "env"]) != 0:
            raise SystemExit("could not make the Python environment")
    step("the Python packages (faster-whisper is ~100 MB)")
    sh([PY, "-m", "pip", "install", "-q", "--upgrade", "pip"], env=env)
    if sh([PY, "-m", "pip", "install", "-q", "-r", "requirements.txt"], env=env) != 0:
        raise SystemExit("the packages did not install - see above")
    if shutil.which("nvidia-smi") or (WIN and os.path.exists(r"C:\Windows\System32\nvidia-smi.exe")):
        step("an NVIDIA card: the CUDA libraries Whisper uses")
        sh([PY, "-m", "pip", "install", "-q", "nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"], env=env)
    have_ff = subprocess.run([PY, "-c", "from live_factory import machine;import sys;"
                                        "sys.exit(0 if machine.find_tool('ffmpeg') else 1)"], cwd=HERE, env=env).returncode == 0
    if not have_ff:
        step("ffmpeg")
        if WIN:
            sh(["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--accept-source-agreements",
                "--accept-package-agreements"])
        elif sys.platform == "darwin" and shutil.which("brew"):
            sh(["brew", "install", "ffmpeg"])
        else:
            print("   install ffmpeg (Mac: brew install ffmpeg; Linux: sudo apt install ffmpeg), then run this again")
    local = os.path.join(HERE, "settings.local.yml")
    if not os.path.exists(local):
        with open(local, "w", encoding="utf-8") as f:
            f.write("# this machine only (git-ignored). Anything from settings.yml can be overridden here.\n"
                    "machine_name: %s\n# work_root: D:/video-factory    # where the \"video factory - <event>\" folders "
                    "go; default: the Desktop\n" % socket.gethostname())
        print("   wrote settings.local.yml")
    if not WIN:
        for f in ("run.sh", "setup.sh"):
            p = os.path.join(HERE, f)
            if os.path.exists(p):
                os.chmod(p, os.stat(p).st_mode | 0o111)
    step("the health check")
    sh([PY, "-m", "live_factory", "doctor"], env=env)
    print("\nNext: the sign-ins from the person's Drive vault (Claude: the live-factory skill, \"A new computer\").")


if __name__ == "__main__":
    main()
