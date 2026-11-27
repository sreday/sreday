"""The factory's own look at a finished talk, in place of Marek's review in Descript when the event runs on autopilot
(Marek 2026-11-27: "instead of asking for permission to run this further, you'll ship it and write a post-mortem").
Nothing goes to YouTube that fails it; the reasons go to the ship report.

check(final, t, cfg, ends_rec) -> (problems, notes) for the published, loudness-normalised MP4:
  - the first and last words heard again (ends.verify on the final, after Descript's gap and filler edits);
  - the length: Descript only shortens gaps and fillers, so the final is a bit shorter than the cut - not much
    shorter (a lost piece) and never longer;
  - sound all the way: no long silence (a muted mic, a lost stretch) - Descript has already shortened every pause;
  - the picture: no long black stretch;
  - loudness and peak (the qa step's numbers);
  - the edit report, the chapters and the title.
"""
import re

from . import ends, media

SILENCE_DB = -50          # under this the final is silent (its loudness is normalised to -14 LUFS)
SILENCE_PROBLEM = 8.0     # seconds of silence that are a problem; Descript shortens real pauses to about 1 s
SILENCE_NOTE = 4.0
BLACK_PROBLEM = 10.0      # seconds of black picture
MIN_KEEP = 0.75           # the final keeps at least this share of the cut
MAX_KEEP = 1.02           # ... and is never longer than it


def silences(path, noise_db=SILENCE_DB, least=SILENCE_NOTE):
    """[(start, length)] of the stretches under noise_db for at least `least` seconds."""
    err = media.run([media.tool("ffmpeg"), "-hide_banner", "-nostats", "-i", path, "-vn", "-af",
                     "silencedetect=noise=%ddB:d=%s" % (noise_db, least), "-f", "null", "-"]).stderr or ""
    out = []
    for m in re.finditer(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", err):
        end, length = float(m.group(1)), float(m.group(2))
        out.append((round(end - length, 1), round(length, 1)))
    return out


def blacks(path, least=BLACK_PROBLEM):
    """[(start, length)] of black picture for at least `least` seconds (sampled at 2 frames a second, small)."""
    err = media.run([media.tool("ffmpeg"), "-hide_banner", "-nostats", "-i", path, "-an", "-vf",
                     "fps=2,scale=320:-2,blackdetect=d=%s:pix_th=0.10" % least, "-f", "null", "-"]).stderr or ""
    return [(round(float(a), 1), round(float(d), 1)) for a, d in
            re.findall(r"black_start:([0-9.]+)\s+black_end:[0-9.]+\s+black_duration:([0-9.]+)", err)]


def stamp(s):
    s = int(s)
    return "%d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60)


def check(final, t, cfg, ends_rec=None, verify=None, lean=None):
    """(problems, notes) - problems keep the talk off YouTube."""
    problems, notes = [], []
    info = media.probe(final)
    dur = info["duration"]
    cut_len = (t.get("probe") or {}).get("duration") or 0
    if not info["width"] or not info["channels"]:
        problems.append("the final has no %s" % ("picture" if not info["width"] else "sound"))
        return problems, notes
    if cut_len:
        share = dur / cut_len
        if share < MIN_KEEP:
            problems.append("the final is %.0f%% of the cut (%s of %s) - Descript took out more than gaps and fillers"
                            % (100 * share, stamp(dur), stamp(cut_len)))
        elif share > MAX_KEEP:
            problems.append("the final is longer than the cut (%s, the cut %s)" % (stamp(dur), stamp(cut_len)))
        else:
            notes.append("%s (the cut %s, %.0f%% kept)" % (stamp(dur), stamp(cut_len), 100 * share))
    for a, n in silences(final):
        (problems if n >= SILENCE_PROBLEM else notes).append("%.0f s of silence at %s" % (n, stamp(a)))
    bl = blacks(final)
    if bl and lean:
        lean_black = sum(n for _, n in blacks(lean))     # the recording itself was black there: not Descript's doing
        if lean_black >= sum(n for _, n in bl) * 0.8:
            notes += ["%.0f s of black picture at %s (in the recording itself)" % (n, stamp(a)) for a, n in bl]
            bl = []
    problems += ["%.0f s of black picture at %s" % (n, stamp(a)) for a, n in bl]
    qa = (t.get("steps") or {}).get("qa") or {}
    problems += ["qa: %s" % f for f in qa.get("flags") or [] if not f.startswith("edit removed")]
    if qa.get("peak") is not None and qa["peak"] > float(cfg.get("true_peak_db") or -1) + 0.5:
        problems.append("the peak is %.1f dBTP" % qa["peak"])
    if ends_rec and ends_rec.get("first") and ends_rec.get("last"):
        p, n = (verify or ends.verify)(final, ends_rec, cfg)
        problems += ["edges: %s" % x for x in p]
        notes += n
    report = str(((t.get("steps") or {}).get("edit") or {}).get("report") or "")
    if re.search(r"\b(could not|couldn'?t|unable to|failed to|error)\b", report, re.I):
        notes.append("the Descript edit report says: %s" % re.sub(r"\s+", " ", report)[:200])
    ch = ((t.get("steps") or {}).get("chapters") or {}).get("chapters") or []
    if len(ch) < 3:
        notes.append("no chapters (Descript gave %d) - the description goes without them" % len(ch))
    return problems, notes
