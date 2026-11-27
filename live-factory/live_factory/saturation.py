"""Saturated (clipped) sound: find it, and put the peaks back.

A mic or a recorder driven too hard cuts every loud peak flat at its ceiling. After the cut the sound is AAC again,
so the plateau is blurred - but its shape still gives it away: the waveform's shoulders on both sides of a flattened
peak point far above its top, while a natural peak (or one a limiter turned down) keeps its dome. Per peak near the
talk's ceiling, a parabola is fitted to the 8 samples before and after it; when its apex lies 25%+ above the top, the
peak was cut - if its top is also flat (12+ samples, most of them within 3% of the top: a cusp or a dome is not).
Measured on real speech (SREday Dallas, Girish Konda, clipped on purpose, then AAC):
  clean, or a limiter holding 14-20 dB of peaks      0-32 such peaks a minute
  clipped by 11 / 12 / 14 / 20 dB (0.4-9% of samples) 44 / 124 / 418 / 1344 a minute
and on all 33 real talks of SREday Dallas and PLATFORMday SF (none clipped): at most 4 in their worst minute.
A minute with CLIPPED_PER_MIN or more counts as clipped; a talk with SATURATED_MINUTES of them is saturated.

The fix (when installed): BetterDeclipper (github.com/Qupci/BetterDeclipper, MIT) - a consistent declipper (every
sample that was not clipped stays exactly as it is; the clipped ones are rebuilt above the ceiling from sparse
time-frequency models), with an analysis that tells digital clipping, clipping blurred by AAC/MP3 ("smeared") and a
limiter apart. It runs on the GPU (torch). Without it the talk still gets Studio Sound at
saturated_studio_sound_percent in Descript.
"""
import json
import os
import subprocess
import sys

import numpy as np

from . import media

RATE = 48000
NEAR = 0.85              # peaks above this share of the ceiling are looked at
SHOULDER = 8             # samples each side fitted with the parabola
APEX = 1.25              # apex / top above this = a peak cut flat ...
MIN_RUN = 12             # ... held near the top for 12+ samples (0.25 ms; a pointed peak is shorter) ...
FLAT = 0.6               # ... with 60%+ of them within 3% of the top (a flat top, not a dome or a cusp)
CLIPPED_PER_MIN = 40     # flagged peaks in a minute that make it a clipped minute
SATURATED_MINUTES = 2    # clipped minutes that make the talk saturated
BLOCK = 600              # seconds decoded at a time
LOCAL = 5                # seconds that share a ceiling
QUIET = 0.03             # a stretch peaking 30 dB under the loudest is not looked at


def _pcm(src, start=None, end=None):
    cmd = [media.tool("ffmpeg"), "-v", "error"]
    if start:
        cmd += ["-ss", "%.3f" % start]
    cmd += ["-i", src]
    if end is not None:
        cmd += ["-t", "%.3f" % (end - (start or 0))]
    cmd += ["-vn", "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    return x[: len(x) // 2 * 2].reshape(-1, 2).astype(np.float64)


def _flagged(s, ceiling):
    """sample positions of the peaks of s (one polarity) that were cut flat."""
    m = s >= NEAR * ceiling
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    a, b = np.where(d == 1)[0], np.where(d == -1)[0]
    K = SHOULDER
    keep = (a >= K) & (b <= len(s) - K) & (b - a >= MIN_RUN)
    out = []
    for i, j in zip(a[keep], b[keep]):
        t = np.r_[np.arange(i - K, i), np.arange(j, j + K)]
        v = s[t]
        if v.min() < 0.2 * ceiling:
            continue
        c = np.polyfit(t - (i + j) / 2.0, v, 2)
        if c[0] >= 0:
            continue
        top = s[i:j].max()
        if c[2] - c[1] ** 2 / (4 * c[0]) > APEX * top and np.mean(s[i:j] >= 0.97 * top) >= FLAT:
            out.append(i)
    return out


def counts(x):
    """x: (n, 2) float samples at RATE. Cut peaks per channel per 10 s: array (2, tens). The ceiling is taken per LOCAL
    seconds: an auto gain (Zoom) or a fader can turn a clipped stretch down below the rest of the talk."""
    tens = int(np.ceil(len(x) / RATE / 10.0)) or 1
    out = np.zeros((x.shape[1], tens), int)
    loud = float(np.quantile(np.abs(x), 0.9999)) if len(x) else 0.0
    for ch in range(x.shape[1]):
        c = x[:, ch]
        if not len(c) or np.abs(c).max() < 1e-4:
            continue
        if ch == 1 and np.allclose(c, x[:, 0], atol=1e-6):
            out[1] = out[0]                            # dual mono: one channel says it all
            continue
        n = LOCAL * RATE
        for b0 in range(0, len(c), n):
            seg = c[max(0, b0 - SHOULDER): b0 + n + SHOULDER]
            ceiling = float(np.quantile(np.abs(seg), 0.9999)) if len(seg) else 0.0
            if ceiling <= max(1e-3, loud * QUIET):
                continue                               # silence, room noise: no speech to clip
            pos = np.array(_flagged(seg, ceiling) + _flagged(-seg, ceiling), int) + max(0, b0 - SHOULDER)
            pos = pos[(pos >= b0) & (pos < b0 + n)]
            if len(pos):
                out[ch] += np.bincount(pos // (RATE * 10), minlength=tens)[:tens]
    return out


def _per_min(tens):
    tens = np.asarray(tens)
    pad = (-len(tens)) % 6
    return np.pad(tens, (0, pad)).reshape(-1, 6).sum(1).tolist()


def scan_pcm(x):
    """Cut peaks per minute (both channels; a dual-mono pair counts once)."""
    c = counts(x)
    both = c[0] if c.shape[0] < 2 or np.array_equal(c[0], c[1]) else c.max(0)
    return _per_min(both)


def scan(src, start=None, end=None):
    """Clipping in [start, end] of src (whole file without them). Returns a summary dict, with "tens" (cut peaks per
    10 s) and "channels" (a summary per channel)."""
    s0 = start or 0.0
    if end is None:
        end = media.probe(src)["duration"]
    parts, rms = [], []
    t = s0
    while t < end - 0.5:
        e = min(end, t + BLOCK)                        # BLOCK is a whole number of minutes
        x = _pcm(src, t, e)
        if not len(x):
            break
        parts.append(counts(x)[:, : int(np.ceil((e - t) / 10.0))])
        rms.append((x ** 2).mean(0) * len(x))
        t = e
    c = np.concatenate(parts, 1) if parts else np.zeros((2, 0), int)
    both = c[0] if np.array_equal(c[0], c[1]) else c.max(0)
    res = summary(_per_min(both) if len(both) else [], s0)
    res["tens"] = both.tolist()
    res["channels"] = [summary(_per_min(ch), s0) for ch in c] if not np.array_equal(c[0], c[1]) else None
    if res["channels"] and rms:
        tot = np.sum(rms, 0) / max(1, int(round((end - s0) * RATE)))
        for d, v in zip(res["channels"], tot):
            d["level_db"] = round(10 * np.log10(v + 1e-12), 1)
    return res


def summary(per_min, start=0.0):
    bad = [i for i, n in enumerate(per_min) if n >= CLIPPED_PER_MIN]
    mins = max(len(per_min), 1)
    return {"per_min": per_min, "clipped_minutes": len(bad), "minutes": len(per_min),
            "worst": max(per_min) if per_min else 0, "rate": round(sum(per_min) / mins, 1),
            "where": [round(start + 60 * i) for i in bad],
            "saturated": len(bad) >= SATURATED_MINUTES, "some": bool(bad)}


def worst_stretch(scan_res, seconds=30):
    """Start (seconds into the scanned span) of the `seconds` with the most cut peaks - the sample to try tools on."""
    tens = np.asarray(scan_res.get("tens") or [0])
    k = max(1, int(seconds // 10))
    if len(tens) <= k:
        return 0.0
    sums = np.convolve(tens, np.ones(k, int), "valid")
    return 10.0 * int(np.argmax(sums))


def describe(s):
    if not s or not s.get("some"):
        return "no clipping"
    return "%s: %d of %d minutes clipped (worst %d cut peaks a minute)" % (
        "SATURATED" if s["saturated"] else "a little clipping", s["clipped_minutes"], s["minutes"], s["worst"])


# ---- the fix ----------------------------------------------------------------------------------------------------------
# Marek, 2026-11-27: pick the better track first; with no choice, try the open-source tools on a sample and judge
# them; a track they save gets the normal Studio Sound; Descript's Studio Sound 100% only as the last resort.
# The tools run in their own environment (torch is big and must not disturb Whisper): local/declip-env, made by
# requirements-declip.txt. Each tool is "<name>:<setting>".
TOOLS = ["betterdeclipper:normal", "voicefixer:0"]


def tool_python(cfg=None):
    """The declip environment's python, or None when it is not set up."""
    p = (cfg or {}).get("declip_python") or os.environ.get("LIVE_FACTORY_DECLIP_PYTHON")
    if not p:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = os.path.join(root, "local", "declip-env", "Scripts" if os.name == "nt" else "bin",
                         "python.exe" if os.name == "nt" else "python")
    return p if os.path.exists(p) else None


def tools_available(cfg=None):
    py = tool_python(cfg)
    if not py:
        return []
    r = subprocess.run([py, "-c", "import importlib.util as u; print(' '.join(n for n in ('torch', 'betterdeclipper', "
                        "'voicefixer') if u.find_spec(n)))"], capture_output=True, text=True)
    have = set(r.stdout.split())
    return [t for t in (cfg or {}).get("declip_tools") or TOOLS if "torch" in have and t.split(":")[0] in have]


def run_tool(tool, wav_in, wav_out, cfg=None, timeout=6 * 3600):
    """One tool on WAVs (wav_in/wav_out: a path or lists of paths), in the declip environment's own process (a CUDA
    failure must not take the run down; the model loads once for all the files). Writes 48 kHz stereo float WAVs at
    the input's level. Returns its printout."""
    py = tool_python(cfg)
    if not py:
        raise RuntimeError("no declip environment (requirements-declip.txt)")
    ins = [wav_in] if isinstance(wav_in, str) else list(wav_in)
    outs = [wav_out] if isinstance(wav_out, str) else list(wav_out)
    job = os.path.abspath(outs[0]) + ".job.json"
    with open(job, "w", encoding="utf-8") as f:
        json.dump([[os.path.abspath(i), os.path.abspath(o)] for i, o in zip(ins, outs)], f)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        r = subprocess.run([py, "-m", "live_factory.saturation", "tool", tool, job], cwd=root,
                           capture_output=True, text=True, timeout=timeout)
    finally:
        os.remove(job)
    if r.returncode != 0 or not all(os.path.exists(o) for o in outs):
        raise RuntimeError("%s failed: %s" % (tool, (r.stderr or r.stdout)[-600:]))
    return [l for l in r.stdout.splitlines() if l.strip()]


def _tool_main(tool, job):
    import soundfile as sf
    name, _, setting = tool.partition(":")
    with open(job, encoding="utf-8") as f:
        pairs = json.load(f)
    if name == "betterdeclipper":
        from betterdeclipper import declip
        from betterdeclipper.cli import analysis_lines

        def fix(y, sr):
            x, info = declip(y, sr, preset=setting or "normal", mode="auto")
            print("; ".join(analysis_lines(info, y.shape[1])))
            return x
    elif name == "voicefixer":
        # a speech restorer: it hears the speech and makes it again through a vocoder (44.1 kHz, mono)
        import torch
        from scipy.signal import resample_poly
        from voicefixer import VoiceFixer
        vf = VoiceFixer()

        def fix(y, sr):
            mono = y.mean(axis=1)
            w = resample_poly(mono, 147, 160) if sr == 48000 else mono
            out = vf.restore_inmem(w.astype(np.float32), cuda=torch.cuda.is_available(), mode=int(setting or 0))
            out = np.asarray(out, np.float64).reshape(-1)
            out = resample_poly(out, 160, 147) if sr == 48000 else out
            out = np.pad(out, (0, max(0, len(mono) - len(out))))[:len(mono)]
            rin, rout = np.sqrt((mono ** 2).mean()), np.sqrt((out ** 2).mean())
            return np.stack([out, out], 1) * (rin / rout if rout > 1e-9 else 1.0)   # at the input's level
    else:
        raise SystemExit("unknown tool " + tool)
    for wav_in, wav_out in pairs:
        y, sr = sf.read(wav_in, always_2d=True, dtype="float64")
        sf.write(wav_out, fix(y, sr).astype(np.float32), sr, subtype="FLOAT")


def to_wav(src, dst, start=None, end=None):
    cmd = [media.tool("ffmpeg"), "-v", "error", "-y"] + (["-ss", "%.3f" % start] if start else []) + ["-i", src]
    if end is not None:
        cmd += ["-t", "%.3f" % (end - (start or 0))]
    subprocess.run(cmd + ["-vn", "-ac", "2", "-ar", str(RATE), "-c:a", "pcm_f32le", dst], check=True)


def remux(video, wav, dst):
    """video's picture with wav as its sound."""
    subprocess.run([media.tool("ffmpeg"), "-v", "error", "-y", "-i", video, "-i", wav, "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(RATE), "-ac", "2",
                    "-movflags", "+faststart", dst], check=True)


# ---- the trial: each tool on a sample of the worst clipped speech, judged ---------------------------------------------
SAMPLE_SECONDS = 30
KEEP_WORDS = 0.4         # a tool may change at most 40% of the words Whisper heard in the clipped sample (plus
                         # twice what Whisper's word probability rose: repaired words do change) ...
CONFIDENCE_SLACK = 0.005  # ... and Whisper must be at least as sure of the words (mean word probability) ...
PEAKS_LEFT = 0.25        # ... and at most a quarter of the cut peaks may be left
# Calibrated on Girish Konda's speech clipped 20 / 26 dB (then AAC), 30 s: BetterDeclipper took the cut peaks from
# 720 / 1228 a minute to 0, Whisper's word probability 0.704 -> 0.728 / 0.648 -> 0.717, changed 24% / 37% of the words
# (back towards the clean original: word error vs the clean words 26% -> 18% / 43% -> 21%; clean vs itself, another
# Whisper pass: ~21%). VoiceFixer (a vocoder that makes the speech again) took the peaks away too, but Whisper's word
# probability fell to 0.60 / 0.62 and 71% / 65% of the words changed ("the third group of mn alerts" -> "the third
# group of pokemon"; on the CLEAN speech too: 68%) - it is tried, and judged out. On the 3 clipped minutes inside
# Girish's real talk: BetterDeclipper 388 -> 0 cut peaks, 0.68 -> 0.71, 29% changed (passes); VoiceFixer 388 -> 0,
# 0.68 -> 0.68, 47% changed (out since KEEP_WORDS 0.4; at 0.5 it passed).


def _words(path, cfg):
    import re
    from . import analyse
    whisper, _ = analyse.model(cfg or {})
    segs, _ = whisper.transcribe(path, language=(cfg or {}).get("whisper_language") or None, beam_size=5,
                                 condition_on_previous_text=False, word_timestamps=True, vad_filter=False)
    ws, pr = [], []
    for sg in segs:
        for w in sg.words or []:
            t = re.sub(r"[^\w']", "", w.word.lower())
            if t:
                ws.append(t)
                pr.append(w.probability)
    return ws, (float(np.mean(pr)) if pr else 0.0)


def word_change(ref, hyp):
    """Word error rate of hyp against ref (edits / words of ref)."""
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d = d, [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (r != h))
    return d[-1] / max(1, len(ref))


def _sample_rate_of_peaks(wav):
    x = _pcm(wav)
    return float(sum(scan_pcm(x))) * 60.0 / max(1.0, len(x) / RATE)


def trial(sample_wav, folder, cfg=None, tools=None, log=None):
    """Every tool on the sample; Whisper and the clipping check judge each. Returns {"base", "results", "best"}; the
    outputs stay in folder (<tool>.wav) for a listen."""
    os.makedirs(folder, exist_ok=True)
    words, conf = _words(sample_wav, cfg)
    base = {"peaks_per_min": round(_sample_rate_of_peaks(sample_wav)), "words": len(words), "confidence": round(conf, 3),
            "text": " ".join(words)}
    results = []
    for tool in tools if tools is not None else tools_available(cfg):
        out = os.path.join(folder, tool.replace(":", "-") + ".wav")
        r = {"tool": tool}
        try:
            r["printout"] = run_tool(tool, sample_wav, out, cfg)
            w, c = _words(out, cfg)
            r.update(peaks_per_min=round(_sample_rate_of_peaks(out)), confidence=round(c, 3),
                     words_changed=round(word_change(words, w), 3), text=" ".join(w))
            why = []
            if r["peaks_per_min"] > PEAKS_LEFT * max(1, base["peaks_per_min"]):
                why.append("cut peaks left: %d of %d a minute" % (r["peaks_per_min"], base["peaks_per_min"]))
            if c < conf - CONFIDENCE_SLACK:
                why.append("Whisper less sure of the words (%.2f, was %.2f)" % (c, conf))
            if r["words_changed"] > KEEP_WORDS + 2 * max(0.0, c - conf):
                why.append("%d%% of the words changed" % (100 * r["words_changed"]))
            if len(words) < 10:
                why.append("too little speech in the sample to judge")
            r["good"], r["why"] = not why, "; ".join(why) or "better: %d -> %d cut peaks a minute, Whisper %.2f -> %.2f, " \
                "%d%% of the words changed" % (base["peaks_per_min"], r["peaks_per_min"], conf, c, 100 * r["words_changed"])
        except Exception as ex:
            r["good"], r["why"] = False, "failed: %s" % str(ex)[-200:]
        if log:
            log("%s: %s" % (tool, r["why"]))
        results.append(r)
    good = [r for r in results if r["good"]]
    best = max(good, key=lambda r: (r["confidence"], -r["peaks_per_min"]))["tool"] if good else None
    res = {"base": base, "results": results, "best": best}
    save(os.path.join(folder, "trial.json"), res)
    return res


def rescue(lean, folder, cfg=None, before=None, log=None):
    """The whole repair of a cut with clipping: the worst SAMPLE_SECONDS of it through every tool (trial), and the
    best tool that passed on the whole sound. Returns a record: before, trial, tool, after, saved (True when the
    whole talk came out with no clipped minute left - or at least no longer saturated)."""
    before = before or scan(lean)
    rec = {"before": before, "tool": None, "saved": False}
    tools = tools_available(cfg)
    if not tools:
        rec["why"] = "no repair tools installed (requirements-declip.txt)"
        return rec
    os.makedirs(folder, exist_ok=True)
    at = worst_stretch(before, SAMPLE_SECONDS)
    sample = os.path.join(folder, "clipped sample.wav")
    to_wav(lean, sample, at, at + SAMPLE_SECONDS)
    rec["sample_at"] = at
    rec["trial"] = tr = trial(sample, folder, cfg, tools, log)
    if not tr["best"]:
        rec["why"] = "no tool made the sample better (%s)" % "; ".join("%s: %s" % (r["tool"], r["why"]) for r in tr["results"])
        return rec
    tmp = os.path.join(folder, "_fixed.mp4")
    try:
        regions = repair(lean, tmp, before, tr["best"], folder, cfg)
        after = scan(tmp)
        rec.update(tool=tr["best"], after=after, regions=regions)
        what = "%s on the %d clipped stretch(es) (%s of the talk)" % (
            tr["best"], len(regions), _mmss(sum(b - a for a, b in regions)))
        if after["clipped_minutes"] < before["clipped_minutes"] and not after["saturated"]:
            os.replace(tmp, lean)
            rec["saved"] = True
            rec["why"] = "%s: now %s" % (what, describe(after))
        else:
            rec["why"] = "%s left it %s - the cut is kept as it was" % (what, describe(after))
    except Exception as ex:                              # noqa: BLE001 - a tool error / out of memory: the cut as it was
        rec["saved"] = False
        rec["why"] = "the repair failed (%s) - the cut is kept as it was, Studio Sound does what it can" % str(ex)[:200]
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return rec


def _mmss(sec):
    return "%d:%02d" % (sec // 60, sec % 60)


def regions(scan_res, total, pad=5.0, merge=20.0, chunk=60.0):
    """The clipped stretches of a scan, padded, as pieces of at most `chunk` seconds (each its own ceiling: an auto
    gain can move the clipping level from minute to minute, and a declipper reads one level per file)."""
    tens = scan_res.get("tens") or []
    hot = [i for i, n in enumerate(tens) if n * 6 >= CLIPPED_PER_MIN // 2]     # a 10 s bin clipped at half the rate
    spans = []
    for i in hot:
        a, b = max(0.0, 10.0 * i - pad), min(total, 10.0 * (i + 1) + pad)
        if spans and a - spans[-1][1] <= merge:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    out = []
    for a, b in spans:
        n = max(1, int(np.ceil((b - a) / chunk)))
        step = (b - a) / n
        out += [(a + k * step, a + (k + 1) * step) for k in range(n)]
    return out


def repair(src, dst, scan_res, tool, folder, cfg=None, fade=0.02):
    """dst = src with the tool's repair on its clipped stretches only (the rest of the sound untouched, sample for
    sample), the picture copied. Returns the stretches [(from, to)]."""
    x = _pcm(src)
    total = len(x) / float(RATE)
    regs = regions(scan_res, total)
    ins, outs = [], []
    for k, (a, b) in enumerate(regs):
        ins.append(os.path.join(folder, "_r%03d_in.wav" % k))
        outs.append(os.path.join(folder, "_r%03d_out.wav" % k))
        _write_wav(ins[-1], x[int(a * RATE):int(b * RATE)])
    try:
        if regs:
            run_tool(tool, ins, outs, cfg)
        y = x.copy()
        nf = int(fade * RATE)
        ramp = np.linspace(0.0, 1.0, nf)[:, None]
        for (a, b), o in zip(regs, outs):
            i0, i1 = int(a * RATE), int(b * RATE)
            r = _pcm(o)[: i1 - i0]
            if len(r) < i1 - i0:
                r = np.pad(r, ((0, i1 - i0 - len(r)), (0, 0)))
            r = r.copy()
            if i0 > 0 and len(r) > 2 * nf:                # a short cross-fade into and out of each repaired stretch
                r[:nf] = r[:nf] * ramp + y[i0:i0 + nf] * (1 - ramp)
            if i1 < len(y) and len(r) > 2 * nf:
                r[-nf:] = r[-nf:] * ramp[::-1] + y[i1 - nf:i1] * (1 - ramp[::-1])
            y[i0:i1] = r
        peak = float(np.abs(y).max()) or 1.0
        if peak > 0.891:                                  # rebuilt peaks rise above the old ceiling: keep -1 dBFS
            y *= 0.891 / peak
        whole = os.path.join(folder, "_whole.wav")
        _write_wav(whole, y)
        remux(src, whole, dst)
        os.remove(whole)
    finally:
        for p_ in ins + outs:
            if os.path.exists(p_):
                os.remove(p_)
    return [(round(a, 1), round(b, 1)) for a, b in regs]


def _write_wav(path, x):
    """(n, 2) float samples -> a float WAV at RATE (by ffmpeg: the factory's own env has no soundfile)."""
    x = np.asarray(x, np.float32)
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    subprocess.run([media.tool("ffmpeg"), "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", str(x.shape[1]),
                    "-i", "-", "-c:a", "pcm_f32le", path], input=x.tobytes(), check=True)


def pick_channel(scan_res):
    """When one channel is clipped and the other is not, an ffmpeg filter that puts the clean one on both."""
    ch = scan_res.get("channels")
    if not ch or len(ch) < 2:
        return None
    a, b = ch
    near = abs((a.get("level_db") or -99) - (b.get("level_db") or -99)) <= 12   # both carry the talk, not one hiss
    if near and a["some"] and not b["some"]:
        return "pan=stereo|c0=c1|c1=c1", 1
    if near and b["some"] and not a["some"]:
        return "pan=stereo|c0=c0|c1=c0", 0
    return None


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "tool":
        _tool_main(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 3 and sys.argv[1] == "scan":
        print(json.dumps(scan(sys.argv[2])))
    else:
        print("python -m live_factory.saturation scan <file> | tool <name:setting> <job.json>")
