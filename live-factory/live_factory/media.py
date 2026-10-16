"""ffprobe / ffmpeg helpers. probe, verify, gpu_status, loudness, early_cut, normalize and pick_resolution are the
Conf42 factory's (conf42/factory/factory/media.py); the rest is for live recordings: per-second channel levels,
audio chunks for Whisper, still frames, a browser proxy, and cutting a talk out of a long recording."""
import json
import os
import re
import shutil
import subprocess
import sys
import time

RESOLUTIONS = [(480, "480p"), (720, "720p"), (1080, "1080p"), (1440, "1440p"), (2160, "4K")]
PROTECTED_FILES, PROTECTED_DIRS = set(), set()   # the raw recordings: nothing here may ever be written or deleted


def protect(path):
    """Register a raw recording (a file) or the folder of raw copies (a folder) as untouchable."""
    real = os.path.realpath(path)
    (PROTECTED_DIRS if os.path.isdir(real) else PROTECTED_FILES).add(os.path.normcase(real))


def safe_target(path):
    """Raise before anything writes to or deletes a raw recording - the only copy of a talk may sit on that drive."""
    real = os.path.normcase(os.path.realpath(path))
    stem = re.sub(r"(\.\d+)?\.part(\.mp4)?$", "", real)           # foo.mp4.123.part.mp4 -> foo.mp4
    if real in PROTECTED_FILES or stem in PROTECTED_FILES or any(
            real == d or real.startswith(d + os.sep) for d in PROTECTED_DIRS):
        raise PermissionError("refusing to write or delete %s - it is a raw recording" % path)
    return path
LEVEL_RATE = 8000          # Hz of the audio read for the channel levels (speech energy is all below 4 kHz)
FLOOR_DB = -90.0           # the level of digital silence in levels.json


def tool(name):
    from . import machine
    found = machine.find_tool(name)
    if found:
        return found
    raise RuntimeError("%s not found - install it with: %s" % (name, machine.install_hint("ffmpeg")))


def run(args):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe(path):
    """width, height, duration, the audio channel count, the codecs and the recording's own clock (creation_time,
    from the camera or recorder - a hint for where in the schedule the file starts)."""
    out = run([tool("ffprobe"), "-v", "error", "-show_entries",
               "stream=codec_type,codec_name,width,height,channels:format=duration,format_name:format_tags=creation_time",
               "-of", "json", path]).stdout
    d = json.loads(out or "{}")
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = d.get("format") or {}
    return {"width": v.get("width") or 0, "height": v.get("height") or 0,
            "duration": float(fmt.get("duration") or 0), "channels": a.get("channels") or 0,
            "vcodec": v.get("codec_name") or "", "acodec": a.get("codec_name") or "",
            "format": fmt.get("format_name") or "", "creation_time": (fmt.get("tags") or {}).get("creation_time") or ""}


def browser_playable(info, path):
    """Can Chrome play the recording as it is (the dashboard seeks in it with a <video>)? H.264 + AAC/MP3 in mp4/mov."""
    return (info.get("vcodec") == "h264" and info.get("acodec") in ("aac", "mp3", "")
            and os.path.splitext(path)[1].lower() in (".mp4", ".m4v", ".mov"))


def _encoders(cq, max_short_side, w, h):
    """The (label, input args, video args) to try on this machine, best first; the CPU always last."""
    big = w and h and min(w, h) > max_short_side
    size = ("-2:%d" % max_short_side) if h <= w else ("%d:-2" % max_short_side)
    scale = ["scale=" + size] if big else []
    nvenc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
    out = []
    if gpu_status():
        out.append(("GPU encoder", [], scale, ["-pix_fmt", "yuv420p"] + nvenc))
    if sys.platform == "darwin":
        q = max(30, min(80, 100 - 2 * cq))                  # cq 21 -> 58, cq 30 -> 40
        out.append(("Mac media engine", ["-hwaccel", "videotoolbox"], scale,
                    ["-pix_fmt", "yuv420p", "-c:v", "h264_videotoolbox", "-q:v", str(q), "-allow_sw", "1"]))
    out.append(("CPU", [], scale, ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(cq - 1)]))
    return out


def encode(src, dst, start=None, end=None, af=None, max_short_side=1080, cq=21, audio_kbps=192, extra_v=None,
           on_progress=None, what="rendering"):
    """Re-encode [start, end] of src to a lean H.264/AAC mp4 (48 kHz stereo), frame-accurate, with an optional audio
    filter (the one-sided stereo fix). Tries the GPU, then the Mac media engine, then the CPU."""
    safe_target(dst)
    info = probe(src)
    length = (end or info["duration"]) - (start or 0)
    cut = (["-ss", "%.3f" % start] if start else []) + (["-to", "%.3f" % end] if end else [])
    audio = ["-map", "0:v:0", "-map", "0:a:0?", "-c:a", "aac", "-b:a", "%dk" % audio_kbps, "-ar", "48000", "-ac", "2",
             "-movflags", "+faststart"] + (["-af", af] if af else [])
    head = ["-hide_banner", "-loglevel", "error", "-y"]
    part = "%s.%d.part.mp4" % (dst, os.getpid())
    err = ""
    for how, hw, vf, venc in _encoders(cq, max_short_side, info["width"], info["height"]):
        args = head + hw + cut + ["-i", src] + (["-vf", ",".join(vf)] if vf else []) + venc + (extra_v or []) + audio
        rc, err = _ffmpeg_progress([tool("ffmpeg")] + args + ["-progress", "pipe:1", "-nostats", part], length,
                                   lambda pct: on_progress and on_progress("%s on the %s - %d%%" % (what, how, pct), pct))
        if rc == 0 and verify(part, length) is None:
            break
        if rc == 0:
            err = "file failed the check: " + str(verify(part, length))
        if os.path.exists(part):
            os.remove(part)
    else:
        raise RuntimeError("%s failed: %s" % (what, err[-300:]))
    os.replace(part, dst)
    return dst


def cut(parts, dst, af=None, max_short_side=1080, on_progress=None, cq=18):
    """One talk out of the recordings: parts = [(src, start, end)] in order (a talk split across two files has two).
    Each part is encoded with the same settings, then joined without another encode. The cut goes to Descript, which
    encodes it again, so it is kept close to the source (cq 18); the light file is the final one (normalize)."""
    safe_target(dst)
    if len(parts) == 1:
        src, a, b = parts[0]
        return encode(src, dst, a, b, af, max_short_side, cq=cq, on_progress=on_progress, what="cutting")
    pieces = []
    try:
        for i, (src, a, b) in enumerate(parts):
            p = "%s.piece%d.mp4" % (dst, i)
            encode(src, p, a, b, af, max_short_side, cq=cq, what="cutting part %d of %d" % (i + 1, len(parts)),
                   on_progress=on_progress)
            pieces.append(p)
        listing = dst + ".concat.txt"
        with open(listing, "w", encoding="utf-8") as f:
            f.write("".join("file '%s'\n" % os.path.abspath(p).replace("'", r"'\''") for p in pieces))
        part = "%s.%d.part.mp4" % (dst, os.getpid())
        r = run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
                 "-c", "copy", "-movflags", "+faststart", part])
        os.remove(listing)
        if r.returncode != 0:
            raise RuntimeError("joining the parts failed: " + r.stderr[-300:])
        os.replace(part, dst)
        return dst
    finally:
        for p in pieces:
            if os.path.exists(p):
                os.remove(p)


def make_proxy(src, dst, on_progress=None):
    """A 480p copy the browser can seek in (keyframe every 2 s), for recordings it cannot play as they are."""
    return encode(src, dst, max_short_side=480, cq=30, audio_kbps=96, extra_v=["-g", "60"], on_progress=on_progress,
                  what="making the review copy")


def levels(src, duration=0, on_progress=None):
    """Per second of the recording: the RMS level (dBFS) of the left and the right channel - what shows a one-sided
    stereo track, the speech in the dashboard's waveform, and the quiet between talks. Mono files give two equal
    channels. Returns {"left": [...], "right": [...]} (one decimal, FLOOR_DB for digital silence)."""
    import numpy as np
    p = subprocess.Popen([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", src, "-vn", "-ac", "2",
                          "-ar", str(LEVEL_RATE), "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    left, right, block, last = [], [], LEVEL_RATE * 4, 0
    while True:
        buf = p.stdout.read(block)
        if not buf:
            break
        if len(buf) < block:
            buf = buf[:len(buf) // 4 * 4]
            if not buf:
                break
        x = np.frombuffer(buf, dtype="<i2").astype(np.float64).reshape(-1, 2) / 32768.0
        for ch, out in ((0, left), (1, right)):
            rms = float(np.sqrt(np.mean(x[:, ch] ** 2)))
            out.append(round(max(FLOOR_DB, 20 * np.log10(rms)) if rms > 0 else FLOOR_DB, 1))
        if on_progress and duration and time.time() - last > 3:
            last = time.time()
            on_progress("reading the audio levels - %d%%" % min(99, len(left) * 100 // max(1, int(duration))),
                        min(99, len(left) * 100 // max(1, int(duration))))
    p.wait()
    return {"left": left, "right": right}


def audio_chunk(src, dst, start, seconds):
    """[start, start + seconds] of the recording as 16 kHz mono WAV for Whisper. Mixed from both channels, so a
    one-sided recording still has its voice (6 dB quieter, which Whisper does not mind)."""
    safe_target(dst)
    r = run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-ss", "%.3f" % start, "-t", "%.3f" % seconds,
             "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst])
    if r.returncode != 0:
        raise RuntimeError("audio extract failed: " + r.stderr[-300:])
    return dst


def stills(src, folder, every):
    """A small JPEG every `every` seconds, from keyframes only (no full decode, so an 8 h file takes minutes):
    folder/<seconds>.jpg. Returns the seconds written."""
    safe_target(folder)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    r = run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-skip_frame", "nokey", "-i", src, "-an",
             "-vf", "fps=1/%d,scale=480:-2" % every, "-q:v", "5", os.path.join(tmp, "%06d.jpg")])
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("frames failed: " + r.stderr[-300:])
    done = []
    for name in sorted(os.listdir(tmp)):
        sec = (int(name[:6]) - 1) * every
        os.replace(os.path.join(tmp, name), os.path.join(folder, "%d.jpg" % sec))
        done.append(sec)
    shutil.rmtree(tmp, ignore_errors=True)
    return done


def still_at(src, seconds, dst, width=640):
    """One frame at `seconds` (fast seek) - for a closer look at a boundary."""
    safe_target(dst)
    r = run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-ss", "%.3f" % seconds, "-i", src,
             "-frames:v", "1", "-vf", "scale=%d:-2" % width, "-q:v", "4", dst])
    if r.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError("frame at %.0f s failed: %s" % (seconds, r.stderr[-200:]))
    return dst


def verify(path, expected_seconds=None):
    """None when the video is sound, else what is wrong: the container must demux with no errors and last as long
    as expected (within 2 s)."""
    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        return "missing or empty"
    r = run([tool("ffmpeg"), "-v", "error", "-i", path, "-map", "0", "-c", "copy", "-f", "null", "-"])
    errors = [l for l in r.stderr.splitlines() if l.strip()]
    if r.returncode != 0 or errors:
        return "%d stream errors, e.g. %s" % (len(errors), (errors or [r.stderr])[0][:120])
    if expected_seconds:
        got = probe(path)["duration"]
        if abs(got - expected_seconds) > 2:
            return "lasts %.0f s instead of %.0f s" % (got, expected_seconds)
    return None


def gpu_status():
    """NVIDIA GPU load; None without nvidia-smi."""
    exe = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if os.name == "nt" else None)
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--query-gpu=name,utilization.gpu,utilization.encoder,temperature.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        v = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
    except Exception:
        return None
    num = lambda x: float(x) if x.replace(".", "", 1).isdigit() else None
    return {"name": v[0], "gpu": num(v[1]), "encoder": num(v[2]), "temp": num(v[3])}


def _ffmpeg_progress(args, duration, cb):
    """Run ffmpeg with -progress pipe:1 and report the percentage done every couple of seconds."""
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    last = 0
    for line in p.stdout:
        if line.startswith("out_time_us=") and duration:
            try:
                pct = min(99, int(int(line.split("=")[1]) / 1e6 / duration * 100))
            except ValueError:
                continue
            if time.time() - last > 2:
                last = time.time()
                cb(pct)
    err = p.stderr.read()
    return p.wait(), err


def pick_resolution(width, height, cap):
    """Largest Descript export size not above the source (short side) and not above the cap."""
    short = min(width, height) if width and height else 1080
    cap_px = dict((label, px) for px, label in RESOLUTIONS).get(cap, 1080)
    best = "480p"
    for px, label in RESOLUTIONS:
        if px <= short and px <= cap_px:
            best = label
    return best


def loudness(path):
    """Integrated loudness (LUFS) and true peak (dBTP) of a file."""
    err = run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
    summary = err[err.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    return (float(i.group(1)) if i else None, float(tp.group(1)) if tp else None)


def early_cut(path, window=12, threshold=0.25):
    """Seconds into the video of the first hard picture change in its first `window` seconds, or None."""
    r = run([tool("ffmpeg"), "-hide_banner", "-nostats", "-t", str(window), "-i", path, "-an",
             "-vf", "scale=320:-1,select='gt(scene\\,%s)',showinfo" % threshold, "-f", "null", "-"])
    m = re.search(r"pts_time:([0-9.]+)", r.stderr or "")
    return float(m.group(1)) if m else None


def normalize(src, dst, lufs, tp, start=None, end=None, crf=None, preset="medium", audio_kbps=160):
    """The finished video: two-pass loudnorm to the target, start / end (seconds) keep only that part (the trim to the
    first and last words). With crf, the picture is re-encoded with x264 at that constant quality - a talk is mostly
    a still stage and slides, so this is a fraction of Descript's export at the same look; should the result come
    out bigger than the export after all, the export's picture is kept as it is."""
    safe_target(dst)
    cut_args = (["-ss", "%.3f" % start] if start else []) + (["-to", "%.3f" % end] if end else [])
    first = run([tool("ffmpeg"), "-hide_banner", "-nostats"] + cut_args + ["-i", src, "-af",
                 "loudnorm=I=%s:TP=%s:LRA=11:print_format=json" % (lufs, tp), "-f", "null", "-"]).stderr
    m = json.loads(first[first.rfind("{"):first.rfind("}") + 1])
    af = ("loudnorm=I={I}:TP={TP}:LRA=11:measured_I={mi}:measured_TP={mtp}:measured_LRA={mlra}:"
          "measured_thresh={mth}:offset={off}:linear=true").format(
        I=lufs, TP=tp, mi=m["input_i"], mtp=m["input_tp"], mlra=m["input_lra"], mth=m["input_thresh"], off=m["target_offset"])
    audio = ["-af", af, "-c:a", "aac", "-b:a", "%dk" % audio_kbps, "-ar", "48000", "-movflags", "+faststart"]
    tmp = "%s.%d.part.mp4" % (dst, os.getpid())
    head = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"] + cut_args + ["-i", src]
    if crf:
        r = run(head + ["-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-profile:v", "high", "-pix_fmt", "yuv420p"]
                + audio + [tmp])
        whole = probe(src)["duration"] or 1
        share = ((end or whole) - (start or 0)) / whole
        if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > os.path.getsize(src) * share:
            os.remove(tmp)                              # bigger than the export: keep its picture instead
            crf = None
    if not crf:
        r = run(head + ["-c:v", "copy"] + audio + [tmp])
    if r.returncode != 0 or not os.path.exists(tmp):
        raise RuntimeError("ffmpeg loudness step failed: " + r.stderr[-400:])
    os.replace(tmp, dst)
    return "x264 crf %s" % crf if crf else "picture as exported"


def sampled_hash(path, size=8 << 20):
    """A fingerprint of a (huge) file from its size and 8 MB at the start, the middle and the end - enough to catch
    a broken copy without reading 30 GB twice."""
    import hashlib
    h, total = hashlib.sha1(), os.path.getsize(path)
    h.update(str(total).encode())
    with open(path, "rb") as f:
        for at in (0, max(0, total // 2 - size // 2), max(0, total - size)):
            f.seek(at)
            h.update(f.read(size))
    return h.hexdigest()


def notify(title, message):
    """A desktop notification (Mac: Notification Centre, Windows: a tray balloon) - for things a background job
    must tell Marek now, like running out of disk space. Never fails."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e", "display notification %s with title %s sound name \"Basso\""
                            % (json.dumps(message), json.dumps(title))], timeout=10)
        elif os.name == "nt":
            ps = ("Add-Type -AssemblyName System.Windows.Forms; $n = New-Object System.Windows.Forms.NotifyIcon; "
                  "$n.Icon = [System.Drawing.SystemIcons]::Warning; $n.Visible = $true; "
                  "$n.ShowBalloonTip(30000, '%s', '%s', 'Warning'); Start-Sleep 30; $n.Dispose()"
                  % (title.replace("'", "''"), message.replace("'", "''")))
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps])
    except Exception:
        pass
