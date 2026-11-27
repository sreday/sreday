"""Separate sound (Marek, 2026-11-27): with a wired set-up the room sound is often recorded on its own device (a
recorder on the mixing desk, a Zoom H-something, the AV team's laptop) while the camera keeps only its built-in mic.
Both hear the same talk, but their clocks don't match: they start and stop at different times, a recorder splits its
files every few GB, one device drifts against the other by a few hundredths of a second an hour.

The factory finds where each piece of sound belongs on each video recording by itself:
  1. a loudness fingerprint of every recording (100 values a second, speech band, log, slow changes removed);
  2. each 2-minute window of the separate sound is slid along every video's fingerprint (FFT cross-correlation) -
     a window that fits gives (video, offset) with a confidence;
  3. windows that agree are joined into stretches, and each stretch gets a straight line video_t = scale * sound_t
     + shift (the slope is the clock drift) fitted on all its windows: lip sync stays within a frame for hours.
Stretches are saved in rec/<sound id>/sync.json; the cut takes the talk's sound from the separate recording
wherever it covers the talk (the camera's own sound fills any hole), unless that sound is silent or clearly worse.
"""
import json
import os
import subprocess

import numpy as np

from . import media

RATE = 100                 # fingerprint values per second (10 ms: lip-sync precision after the line fit)
WINDOW = 120               # seconds of sound matched at a time
STEP = 60                  # seconds between windows
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg", ".opus", ".wma")


# ---- fingerprints ----------------------------------------------------------------------------------------------------
def fingerprint(src, cache=None):
    """The recording's loudness fingerprint: float32, RATE values a second; 0 where it is digitally silent.
    Cached as .npy next to its analysis."""
    if cache and os.path.exists(cache):
        return np.load(cache)
    sr, hop = 8000, 8000 // RATE
    args = [media.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", src, "-vn", "-ac", "1",
            "-af", "highpass=f=150,lowpass=f=3800", "-ar", str(sr), "-f", "s16le", "-"]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    vals, rest = [], np.zeros(0, np.float32)
    try:
        while True:
            buf = p.stdout.read(sr * 2 * 60)                   # a minute at a time
            if not buf:
                break
            x = np.concatenate([rest, np.frombuffer(buf, np.int16).astype(np.float32)])
            n = len(x) // hop
            fr = x[:n * hop].reshape(n, hop)
            vals.append(np.sqrt((fr ** 2).mean(axis=1)))
            rest = x[n * hop:]
    finally:
        p.stdout.close()
        p.wait()
    rms = np.concatenate(vals) if vals else np.zeros(0, np.float32)
    silent = rms < 2.0                                          # digital silence (a muted channel, a gap)
    e = np.log(rms + 30.0)
    k = RATE                                                    # take away the slow changes (1 s moving mean)
    cs = np.concatenate([[0], np.cumsum(e)])
    lo = np.clip(np.arange(len(e)) - k // 2, 0, len(e))
    hi = np.clip(np.arange(len(e)) + k // 2 + 1, 0, len(e))
    e = e - (cs[hi] - cs[lo]) / np.maximum(hi - lo, 1)
    e = np.clip(e, -2.5, 2.5)
    e[silent] = 0
    e = e.astype(np.float32)
    if cache:
        media.safe_target(cache)
        np.save(cache, e)
    return e


# ---- matching ------------------------------------------------------------------------------------------------------
class Target:
    """A video's fingerprint, ready for many windows: its FFT and running energy for a normalised correlation."""

    def __init__(self, rid, fp):
        self.rid, self.fp = rid, fp
        self.n = 1 << int(np.ceil(np.log2(len(fp) + WINDOW * RATE + 1)))
        self.F = np.fft.rfft(fp, self.n)
        self.cs2 = np.concatenate([[0.0], np.cumsum(fp.astype(np.float64) ** 2)])

    def corr(self, w):
        """Normalised correlation of window w at every lag (w starting at fp[lag])."""
        m = len(w)
        c = np.fft.irfft(np.conj(np.fft.rfft(w, self.n)) * self.F, self.n)[:len(self.fp) - m + 1]
        energy = self.cs2[m:] - self.cs2[:-m]
        return c / (np.sqrt(np.maximum(energy, 1e-9)) * (np.linalg.norm(w) + 1e-9))

    def local(self, w, lag, reach):
        """The correlation only around lag (+- reach samples) - the cheap check once a stretch is found."""
        m = len(w)
        a, b = max(0, lag - reach), min(len(self.fp) - m, lag + reach)
        if b <= a:
            return None, None
        seg = self.fp[a:b + m]
        c = np.correlate(seg, w, mode="valid")
        energy = self.cs2[a + m:b + m + 1] - self.cs2[a:b + 1]
        return c / (np.sqrt(np.maximum(energy, 1e-9)) * (np.linalg.norm(w) + 1e-9)), a


def _peak(c, i):
    """Sub-sample position of the peak at i (parabola through its neighbours)."""
    if 0 < i < len(c) - 1:
        y0, y1, y2 = c[i - 1], c[i], c[i + 1]
        d = y0 - 2 * y1 + y2
        if d < 0:
            return i + 0.5 * (y0 - y2) / d
    return float(i)


def windows(sound, targets, on_progress=None, min_corr=0.25, min_z=9.0):
    """Every window of the sound fingerprint matched against the targets: [{"a" (window start, s), "rec", "lag"
    (where it starts in that video, s), "corr", "z"}] - only the windows that fit somewhere."""
    m, step = WINDOW * RATE, STEP * RATE
    starts = list(range(0, max(1, len(sound) - m + 1), step))
    if len(sound) > m and starts[-1] + m < len(sound):
        starts.append(len(sound) - m)
    out, last = [], None
    for k, s0 in enumerate(starts):
        w = sound[s0:s0 + m]
        if np.count_nonzero(w) < 0.3 * m or w.std() < 0.05:   # mostly silence: nothing to match on
            continue
        hit = None
        if last is not None:                                  # the same stretch carrying on: look nearby only
            t, lag0 = last
            c, a = t.local(w, lag0 + (s0 - last_s0), 3 * RATE)
            if c is not None and len(c):
                i = int(np.argmax(c))
                if c[i] >= max(min_corr, 0.35):
                    hit = {"rec": t.rid, "lag": (a + _peak(c, i)) / RATE, "corr": float(c[i]), "z": None}
        if hit is None:
            best = None
            for t in targets:
                if len(t.fp) < m:
                    continue
                c = t.corr(w)
                i = int(np.argmax(c))
                sd = float(c.std()) or 1e-9
                z = (float(c[i]) - float(np.median(c))) / sd
                if c[i] >= min_corr and z >= min_z and (best is None or c[i] > best[1]):
                    best = (t, float(c[i]), z, _peak(c, i))
            if best:
                t, cv, z, pk = best
                hit = {"rec": t.rid, "lag": pk / RATE, "corr": cv, "z": round(z, 1)}
        if hit:
            hit["a"] = s0 / RATE
            out.append(hit)
            last, last_s0 = (next(t for t in targets if t.rid == hit["rec"]), int(round(hit["lag"] * RATE))), s0
        else:
            last = None
        if on_progress and k % 10 == 0:
            on_progress("matching the sound - %d%%" % (100 * k // len(starts)), 100 * k // len(starts))
    return out


def stretches(wins, length, max_jump=0.15, min_windows=2):
    """Join agreeing windows into stretches with a fitted line video_t = scale * sound_t + shift.
    [{"rec", "a0", "a1" (sound seconds), "v0", "v1" (video seconds), "scale", "shift", "windows", "error_ms"}]."""
    groups = []
    for w in sorted(wins, key=lambda x: x["a"]):
        off = w["lag"] - w["a"]
        g = groups[-1] if groups else None
        if g and g["rec"] == w["rec"] and abs(off - g["off"]) <= max_jump + 2e-4 * (w["a"] - g["w"][-1]["a"]):
            g["w"].append(w)
            g["off"] = off
        else:
            groups.append({"rec": w["rec"], "off": off, "w": [w]})
    out = []
    for g in groups:
        ws = g["w"]
        if len(ws) < min_windows and not (len(ws) == 1 and ws[0]["corr"] >= 0.6):
            continue
        a = np.array([w["a"] + WINDOW / 2 for w in ws])
        v = np.array([w["lag"] + WINDOW / 2 for w in ws])
        if len(ws) >= 3:
            scale, shift = np.polyfit(a, v, 1)
            if abs(scale - 1) > 0.002:                      # no real clock is that far off: a bad fit
                scale, shift = 1.0, float(np.median(v - a))
        else:
            scale, shift = 1.0, float(np.median(v - a))
        err = float(np.abs(v - (scale * a + shift)).max()) * 1000
        a0 = max(0.0, ws[0]["a"])
        a1 = min(length, ws[-1]["a"] + WINDOW)
        out.append({"rec": g["rec"], "a0": round(a0, 2), "a1": round(a1, 2),
                    "v0": round(scale * a0 + shift, 3), "v1": round(scale * a1 + shift, 3),
                    "scale": float(scale), "shift": float(shift), "windows": len(ws),
                    "corr": round(float(np.median([w["corr"] for w in ws])), 3), "error_ms": round(err, 1)})
    return out


def sync(sound_rid, sound_src, sound_fp_cache, videos, on_progress=None):
    """Where one separate sound recording sits on the videos. videos = [(rid, src, fingerprint cache)].
    Returns {"stretches": [...], "length": s, "drift_ppm": [...], "notes": [...]}."""
    fp = fingerprint(sound_src, sound_fp_cache)
    targets = []
    for rid, src, cache in videos:
        if on_progress:
            on_progress("reading the sound of %s" % rid, None)
        targets.append(Target(rid, fingerprint(src, cache)))
    wins = windows(fp, targets, on_progress)
    st = stretches(wins, len(fp) / RATE)
    notes = []
    covered = sum(s["a1"] - s["a0"] for s in st)
    notes.append("%d stretch(es) found, %d%% of the sound placed" % (len(st), 100 * covered / max(1.0, len(fp) / RATE)))
    for s in st:
        notes.append("%s %s-%s of the sound -> %s %s-%s (drift %+.0f ppm, fit within %.0f ms, %d windows)" % (
            sound_rid, _hms(s["a0"]), _hms(s["a1"]), s["rec"], _hms(s["v0"]), _hms(s["v1"]),
            (s["scale"] - 1) * 1e6, s["error_ms"], s["windows"]))
    return {"stretches": st, "length": round(len(fp) / RATE, 2), "windows": len(wins), "notes": notes}


def _hms(s):
    s = int(round(s))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


# ---- using it in a cut -----------------------------------------------------------------------------------------------
def pieces(syncs, rec, start, end):
    """The separate sound covering [start, end] of video rec: [(sound rid, stretch, v_from, v_to)] in time order,
    non-overlapping (where two cover the same moment, the earlier one keeps it)."""
    cand = []
    for sid, data in syncs.items():
        for s in data.get("stretches") or []:
            if s["rec"] == rec and s["v1"] > start and s["v0"] < end:
                cand.append((max(start, s["v0"]), min(end, s["v1"]), sid, s))
    cand.sort(key=lambda x: x[0])
    out, t = [], start
    for a, b, sid, s in cand:
        a = max(a, t)
        if b - a >= 1.0:
            out.append((sid, s, a, b))
            t = b
    return out


def level_db(lv, a, b):
    """(speech level, noise floor) in dB over [a, b] seconds of a levels.json: the 90th and 10th percentile of the
    louder channel."""
    if not lv:
        return None, None
    L, R = np.array(lv.get("left") or []), np.array(lv.get("right") or lv.get("left") or [])
    a, b = int(max(0, a)), int(min(len(L), b))
    if b - a < 5:
        return None, None
    x = np.maximum(L[a:b], R[a:b])
    return float(np.percentile(x, 90)), float(np.percentile(x, 10))


def choose(video_lv, sound_lv, v_from, v_to, s, worse_db=6.0):
    """Use the separate sound for [v_from, v_to]? (yes/no, why). No when it is silent there (a muted desk, an
    unplugged channel) or its speech stands out of its noise clearly less than the camera's does."""
    a_from, a_to = (v_from - s["shift"]) / s["scale"], (v_to - s["shift"]) / s["scale"]
    sp_s, fl_s = level_db(sound_lv, a_from, a_to)
    sp_v, fl_v = level_db(video_lv, v_from, v_to)
    if sp_s is None:
        return True, "separate sound (no levels to compare)"
    if sp_s < -55:
        return False, "the separate sound is silent here (%.0f dB) - the camera's sound kept" % sp_s
    if sp_v is not None and (sp_s - fl_s) < (sp_v - fl_v) - worse_db:
        return False, "the separate sound is noisier here (%.0f dB above its floor, the camera %.0f) - camera kept" % (
            sp_s - fl_s, sp_v - fl_v)
    return True, "separate sound (%.0f dB above its floor%s)" % (
        sp_s - fl_s, "" if sp_v is None else ", the camera's %.0f" % (sp_v - fl_v))


def save(path, data):
    media.safe_target(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
