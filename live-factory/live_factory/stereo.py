"""One-sided stereo: a venue mixer often feeds the camera's left input only (or the right), so the talk plays in
one ear. Measured per talk from the recording's per-second channel levels (levels.json), because the microphone
- and so the side - can change during the day. The fix copies the live channel onto the empty one."""
import statistics


def check(lv, start, end, cfg):
    """{"fix": "left"|"right"|None (the channel to copy onto both), "left_db", "right_db", "af"} for [start, end]
    seconds of a recording whose levels are lv = {"left": [...], "right": [...]}. Only seconds with sound in them
    count (louder than -50 dBFS on either side), so long pauses do not drag the medians down."""
    a, b = max(0, int(start)), max(0, int(end))
    left, right = lv["left"][a:b], lv["right"][a:b]
    pairs = [(l, r) for l, r in zip(left, right) if max(l, r) > -50]
    if len(pairs) < 10:
        return {"fix": None, "left_db": None, "right_db": None, "af": None, "note": "too little sound to tell"}
    ml, mr = statistics.median(p[0] for p in pairs), statistics.median(p[1] for p in pairs)
    gap, silent = float(cfg.get("stereo_gap_db", 25)), float(cfg.get("silent_channel_db", -60))
    fix = None
    if mr < silent <= ml or ml - mr >= gap:
        fix = "left"
    elif ml < silent <= mr or mr - ml >= gap:
        fix = "right"
    af = {"left": "pan=stereo|c0=c0|c1=c0", "right": "pan=stereo|c0=c1|c1=c1"}.get(fix)
    return {"fix": fix, "left_db": round(ml, 1), "right_db": round(mr, 1), "af": af,
            "note": ("only the %s channel has sound (L %.0f dB, R %.0f dB) - copied to both" % (fix, ml, mr)) if fix
            else "both channels have sound (L %.0f dB, R %.0f dB)" % (ml, mr)}
