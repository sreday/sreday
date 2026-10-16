"""Everything that differs between machines (copied from the Conf42 factory, conf42/factory/factory/machine.py, and
trimmed): where the Desktop really is, where ffmpeg lives, the Descript token, what the machine can run Whisper on,
and the health check (`doctor`).

The Descript token is never in the repo: DESCRIPT_API_TOKEN (Windows: the user variable set by setx), else the
private file ~/.live-factory/descript_token, else the Conf42 factory's ~/.conf42-factory/descript_token (same
Descript account, so a machine set up for Conf42 needs nothing more).
"""
import glob
import os
import platform
import shutil
import socket
import subprocess
import sys

HOME_DIR = os.path.join(os.path.expanduser("~"), ".live-factory")
TOKEN_FILE = os.path.join(HOME_DIR, "descript_token")
CONF42_TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".conf42-factory", "descript_token")


# ---- places ------------------------------------------------------------------------------------------------------
def desktop():
    """The user's real Desktop folder (on Windows it may live in OneDrive)."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                path = os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
            if os.path.isdir(path):
                return path
        except OSError:
            pass
    elif sys.platform.startswith("linux") and shutil.which("xdg-user-dir"):
        path = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True).stdout.strip()
        if path and os.path.isdir(path):
            return path
    return os.path.join(os.path.expanduser("~"), "Desktop")


def work_root(cfg):
    """Folder holding one sub-folder per event: settings `work_root` (absolute) or Desktop/<work_folder>."""
    root = cfg.get("work_root")
    return os.path.expanduser(root) if root else os.path.join(desktop(), cfg.get("work_folder") or "live-factory")


def final_folder(cfg):
    return os.path.join(work_root(cfg), cfg.get("final_folder") or "final videos for YouTube")


def open_folder(path):
    """Show a folder (or open a file) in Explorer / Finder / the Linux desktop."""
    if not os.path.isfile(path):
        os.makedirs(path, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def find_tool(name):
    """ffmpeg / ffprobe: PATH, then winget's and Homebrew's usual folders."""
    found = shutil.which(name)
    if found:
        return found
    patterns = [os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Packages\*FFmpeg*\*\bin\%s.exe" % name),
                "/opt/homebrew/bin/%s" % name, "/usr/local/bin/%s" % name]
    for pat in patterns:
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return None


def install_hint(what):
    mac, win = sys.platform == "darwin", os.name == "nt"
    return {"ffmpeg": "winget install Gyan.FFmpeg" if win else "brew install ffmpeg" if mac else "sudo apt install ffmpeg",
            "whisper": "pip install -r live-factory/requirements.txt (in the live-factory venv)"}.get(what, "")


def machine_name(cfg):
    return str(cfg.get("machine_name") or socket.gethostname())


# ---- Whisper ------------------------------------------------------------------------------------------------------
def cuda_available():
    """An NVIDIA card CTranslate2 can use (faster-whisper's engine; it has no Apple GPU support, the Mac runs on CPU)."""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def whisper_setup(cfg):
    """(model, device, compute_type) for this machine: large-v3 in float16 on an NVIDIA card, `small` in int8 on the
    CPU - the transcript only has to find names, titles and pauses, and small runs ~10x faster than real time on
    an M1 Max (an 8 h day in under an hour)."""
    model = str(cfg.get("whisper_model") or "auto")
    if cuda_available():
        return (model if model != "auto" else "large-v3"), "cuda", "float16"
    return (model if model != "auto" else "small"), "cpu", "int8"


# ---- the Descript token --------------------------------------------------------------------------------------------
def read_token():
    t = os.environ.get("DESCRIPT_API_TOKEN", "").strip()
    if not t and os.name == "nt":      # a fresh `setx` is not visible to already-open shells: ask the registry
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                t = str(winreg.QueryValueEx(k, "DESCRIPT_API_TOKEN")[0]).strip()
        except OSError:
            t = ""
    for path in (TOKEN_FILE, CONF42_TOKEN_FILE):
        if not t and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                t = f.read().strip()
    return t


def store_token(t):
    """Windows: user environment variable (setx). Elsewhere: a private file only this user can read."""
    t = t.strip()
    if os.name == "nt":
        subprocess.run(["setx", "DESCRIPT_API_TOKEN", t], capture_output=True)
        os.environ["DESCRIPT_API_TOKEN"] = t
        return "Windows user environment variable DESCRIPT_API_TOKEN"
    os.makedirs(HOME_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(t)
    os.chmod(TOKEN_FILE, 0o600)
    return TOKEN_FILE


# ---- health check -------------------------------------------------------------------------------------------------
def doctor(cfg, check_descript=True):
    """Returns [{name, ok, detail, fix, required}] for the dashboard and `python -m live_factory doctor`."""
    from . import events
    out = []
    def add(name, ok, detail="", fix="", required=True):
        out.append({"name": name, "ok": bool(ok), "detail": detail, "fix": fix, "required": required})

    add("Python", sys.version_info >= (3, 9), platform.python_version(), "Python 3.9 or newer")
    missing = []
    for mod in ("requests", "yaml", "numpy", "faster_whisper"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    add("Python packages", not missing, "all there" if not missing else "missing: " + ", ".join(missing),
        install_hint("whisper"))
    ff, fp = find_tool("ffmpeg"), find_tool("ffprobe")
    add("ffmpeg / ffprobe", ff and fp, ff or "not found", install_hint("ffmpeg"))
    model, device, compute = whisper_setup(cfg)
    add("Whisper", True, "%s on %s (%s)" % (model, device.upper(), compute), required=False)
    repos = []
    for brand in sorted(events.brands(cfg)):
        repos.append("%s %s" % (brand, "ok" if os.path.isdir(events.brand_repo(cfg, brand)) else "MISSING"))
    add("Brand repos", all(r.endswith("ok") for r in repos), ", ".join(repos),
        "clone the missing repos next to this one, or set github_root in settings.local.yml", required=False)
    root = work_root(cfg)
    try:
        os.makedirs(root, exist_ok=True)
        free = shutil.disk_usage(root).free / 1e9
        add("Work folder", free > 50, "%s (%.0f GB free)" % (root, free),
            "free some space (a day of recordings needs ~2x its size)")
    except OSError as ex:
        add("Work folder", False, str(ex), "set work_root in live-factory/settings.local.yml")
    tok = read_token()
    if not tok:
        add("Descript token", False, "not set", "python -m live_factory setup")
    elif check_descript:
        try:
            import requests
            r = requests.get("https://descriptapi.com/v1/status", headers={"Authorization": "Bearer " + tok}, timeout=20)
            add("Descript token", r.status_code == 200,
                ("works, drive: %s" % r.json().get("drive_name")) if r.status_code == 200 else "rejected (%s)" % r.status_code,
                "make a new token: Descript > Settings > API tokens, then python -m live_factory setup")
        except Exception as ex:
            add("Descript token", False, "no answer: %s" % ex, "check the internet connection")
    else:
        add("Descript token", True, "set")
    return out
