"""The check before Descript (Marek, 2026-11-27: "double check the image and sound were merged correctly, that the bad
"pilot" sound is successfully suppressed, etc. - sending it to Descript costs us real money").

After a cut is made with deck pages and/or a separate sound laid in, the cut FILE itself is checked against what was
meant to go into it, before anything is uploaded:
  - picture: in every deck-page stretch, frames of the cut show that page (compared with the very image rendered for
    it); in the video stretches, frames show the recording, not a page;
  - sound: where the separate recording covers the talk, short windows of the cut's sound are the recorder's (in sync
    within 20 ms, waveform alike) - the camera's "pilot" sound is gone; where it doesn't, the camera's sound is there
    (not silence); the cut is as long as it should be.
Anything off stops that talk before the upload (no Descript credits spent) with what was wrong; a contact sheet of the
cut itself (cuts/<talk>.check.jpg) is written for a human or Claude to look at either way.
"""
import os
import subprocess

import numpy as np

from . import media

SR = 8000


def _pcm(path, start, seconds):
    r = subprocess.run([media.tool("ffmpeg"), "-v", "error", "-ss", "%.3f" % max(0.0, start), "-t", "%.3f" % seconds,
                        "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32)


def _frame(path, t, w, h):
    r = subprocess.run([media.tool("ffmpeg"), "-v", "error", "-ss", "%.3f" % t, "-i", path, "-frames:v", "1",
                        "-vf", "scale=%d:%d" % (w, h), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    if len(r.stdout) != w * h * 3:
        return None
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3).astype(np.float32)


def _png(path, w, h):
    return _frame(path, 0, w, h)


def _alike(out, ref, reach_ms=100):
    """Best (lag ms, likeness) of out inside ref, ref starting reach_ms earlier and ending reach_ms later."""
    m = int(reach_ms * SR / 1000)
    if len(out) < 200 or len(ref) < len(out) + 2 * m:
        return None, 0.0
    c = np.correlate(ref[:len(out) + 2 * m], out, "valid")
    i = int(np.argmax(c))
    seg = ref[i:i + len(out)]
    den = np.linalg.norm(out) * np.linalg.norm(seg)
    return (m - i) * 1000.0 / SR, float(c[i] / den) if den > 0 else 0.0


def check(lean, parts, extras, size, sheet=None, crop=None):
    """Check the cut file `lean` made from parts [(src, start, end)] with extras [compose() extra per part].
    size = (W, H) of the cut. Returns (problems [str], notes [str])."""
    problems, notes = [], []
    W, H = size
    w, h = 320, max(2, int(round(320 * H / W / 2)) * 2)
    info = media.probe(lean)
    want = sum(b - a for _, a, b in parts)
    if abs(info["duration"] - want) > 1.0:
        problems.append("the cut lasts %.1f s, it should be %.1f s" % (info["duration"], want))
    offset = 0.0
    shots = []
    for (src, a, b), extra in zip(parts, extras):
        extra = extra or {}
        # ---- picture: deck pages where meant, the recording elsewhere
        slides = extra.get("slides")
        spans = []
        if slides:
            lst, expr = slides
            files, durs = [], []
            for line in open(lst, encoding="utf-8").read().splitlines():
                if line.startswith("file "):
                    files.append(line[6:-1].replace("'\\''", "'"))
                elif line.startswith("duration "):
                    durs.append(float(line.split()[1]))
            t = 0.0
            for f, d in zip(files, durs):
                spans.append((t, t + d, f))
                t += d
            on = [tuple(float(x) for x in seg[len("between(t,"):-1].split(",")) for seg in expr.split("+")]
            bad = 0
            for s0, s1 in on:
                if s1 - s0 < 2:
                    continue
                for tm in (s0 + 0.6, (s0 + s1) / 2, s1 - 0.6):
                    img = next((f for x0, x1, f in spans if x0 <= tm < x1), None)
                    fr, pg = _frame(lean, offset + tm, w, h), (_png(img, w, h) if img else None)
                    if fr is None or pg is None:
                        continue
                    d = float(np.abs(fr - pg).mean())
                    if d > 8:
                        bad += 1
                        problems.append("picture: at %s the cut does not show its deck page (difference %.0f)" % (
                            _hms(offset + tm), d))
                shots.append((offset + (s0 + s1) / 2, "deck page"))
            notes.append("picture: %d deck-page stretch(es) checked%s" % (len(on), "" if not bad else ", %d wrong" % bad))
            # one video stretch, if any: it must be the recording, not a page
            gaps, t = [], 0.0
            for s0, s1 in on:
                if s0 - t > 4:
                    gaps.append((t, s0))
                t = max(t, s1)
            if (b - a) - t > 4:
                gaps.append((t, b - a))
            for g0, g1 in gaps[:3]:
                tm = (g0 + g1) / 2
                fr, src_fr = _frame(lean, offset + tm, w, h), _frame(src, a + tm, w, h)
                if fr is not None and src_fr is not None and not extra.get("colour") and not crop:
                    d = float(np.abs(fr - src_fr).mean())
                    if d > 25:
                        problems.append("picture: at %s the cut should show the recording, it differs (%.0f)" % (
                            _hms(offset + tm), d))
                shots.append((offset + tm, "recording"))
        # ---- sound: the recorder's where it covers, the camera's elsewhere
        sound = extra.get("sound")
        if sound:
            leaks, offs, alike_all, pilots = 0, [], [], []
            for p in sound:
                dur = (p["end"] - p["start"]) * p["scale"]
                for k in range(8):
                    tm = p["at"] + dur * (k + 0.5) / 8
                    out = _pcm(lean, offset + tm, 0.4)
                    if np.abs(out).mean() < 20:              # a quiet moment: nothing to compare
                        continue
                    s_t = p["start"] + (tm - p["at"]) / p["scale"]
                    ref = _pcm(p["src"], s_t - 0.1, 0.6)
                    cam = _pcm(src, a + tm - 0.1, 0.6)
                    lag, alike = _alike(out, ref)
                    alike_all.append(alike)
                    if lag is None:
                        continue
                    offs.append(lag)
                    # the cut's sound as a mix of the recorder's and the camera's (least squares): the camera's share
                    # must be next to nothing - that is the pilot sound suppressed
                    m = int(100 * SR / 1000)
                    i = m - int(round(lag * SR / 1000))
                    r = ref[i:i + len(out)]
                    clag, _ = _alike(out, cam)
                    j = m - int(round((clag or 0) * SR / 1000))
                    c = cam[j:j + len(out)]
                    if len(r) == len(out) and len(c) == len(out):
                        coef, *_ = np.linalg.lstsq(np.stack([r, c], 1), out, rcond=None)
                        pilot = float(abs(coef[1]) * np.linalg.norm(c) / (np.linalg.norm(out) + 1e-9))
                    else:
                        pilot = 0.0
                    pilots.append(pilot)
                    if alike < 0.8 or (pilot > 0.12 and alike < 0.97):   # the recorder alone explains it: fine
                        leaks += 1
            if not alike_all:
                problems.append("sound: no speech found to compare where the separate sound was laid in")
            else:
                med = float(np.median(alike_all))
                notes.append("sound: the separate recording, %d window(s) checked, alike %.2f, off by %+.0f ms, camera "
                             "pilot left %.2f" % (len(alike_all), med, float(np.median(offs)) if offs else 0,
                                                  float(np.median(pilots)) if pilots else 0))
                if leaks > len(alike_all) * 0.25 or (pilots and float(np.median(pilots)) > 0.08 and med < 0.97):
                    problems.append("sound: in %d of %d windows the cut's sound is not the separate recording (the "
                                    "camera's pilot sound may be in it)" % (leaks, len(alike_all)))
                if offs and abs(float(np.median(offs))) > 20:
                    problems.append("sound: the separate recording is %.0f ms out of sync" % float(np.median(offs)))
            # where the recorder does not reach, the camera's sound must be there
            covered = [(p["at"], p["at"] + (p["end"] - p["start"]) * p["scale"]) for p in sound]
            t, holes = 0.0, []
            for c0, c1 in sorted(covered):
                if c0 - t > 3:
                    holes.append((t, c0))
                t = max(t, c1)
            if (b - a) - t > 3:
                holes.append((t, b - a))
            for g0, g1 in holes[:3]:
                cam = _pcm(src, a + (g0 + g1) / 2 - 1, 2)
                out = _pcm(lean, offset + (g0 + g1) / 2 - 1, 2)
                if np.abs(cam).mean() > 30 and np.abs(out).mean() < 0.1 * np.abs(cam).mean():
                    problems.append("sound: at %s the camera's sound should fill in, the cut is silent" % _hms(
                        offset + (g0 + g1) / 2))
        offset += b - a
    if sheet:
        try:
            _sheet(lean, shots, info["duration"], sheet, w, h)
        except Exception as ex:                             # the sheet is for looking at; never stops a talk
            notes.append("the check sheet failed: %s" % ex)
    return problems, notes


def _sheet(lean, shots, dur, dst, w, h):
    """12 frames of the cut itself (plus the checked moments), labelled - what Descript will get."""
    import cv2
    times = sorted({round(dur * (k + 0.5) / 12, 1) for k in range(12)} | {round(t, 1) for t, _ in shots[:8]})
    tiles = []
    for t in times[:20]:
        fr = _frame(lean, t, w, h)
        if fr is None:
            continue
        fr = fr.astype(np.uint8).copy()
        cv2.putText(fr, _hms(t), (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)
        tiles.append(fr)
    if not tiles:
        return
    while len(tiles) % 4:
        tiles.append(np.full_like(tiles[0], 255))
    rows = [np.concatenate(tiles[i:i + 4], axis=1) for i in range(0, len(tiles), 4)]
    media.safe_target(dst)
    cv2.imwrite(dst, cv2.cvtColor(np.concatenate(rows, axis=0), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 80])


def _hms(s):
    s = int(round(s))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)
