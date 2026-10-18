"""Score the automatic draft against a hand-checked day (research/<event>.cuts.json) - for tuning segment.py on real
recordings. Needs that event analysed on this machine (its transcript and levels in the work folder).

  env/bin/python tests/realday.py platformday-2026-austin-q4

2026-10-16, PLATFORMday Austin 2026 Q4 (one 8 h 48 min recording, 16 talks): 13 of 15 within 30 s, the no-show found.
The two left are judgement calls - Daniel's voice apology before his first topic line, and MJ's unannounced room
discussion, which the draft keeps (doubt keeps a Q&A).

2026-10-17, LLMday Austin 2026 Q4 (two recordings, 11 talks): 7 of 11 - unannounced Q&As after the closing "thank
you" (Maryam, Nick, Nate) and Vashishtha's closing cut 40 s early (README, "Learned from a real day").
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from live_factory import pipeline, segment  # noqa: E402

event = sys.argv[1] if len(sys.argv) > 1 else "platformday-2026-austin-q4"
with open(os.path.join(HERE, "research", event + ".cuts.json"), encoding="utf-8") as f:
    ref = json.load(f)
run = pipeline.Run(event)
mine = {c["key"]: c["parts"][0] for c in ref["cuts"]}
d = segment.draft(run)
ok = 0
for c in d["cuts"]:
    p, m = c["parts"][0], mine.get(c["key"])
    good = bool(m) and abs(p["start"] - m["start"]) <= 30 and abs(p["end"] - m["end"]) <= 30
    ok += good
    print("%s %-24s %+6.0f / %+6.0f s  %s" % ("ok " if good else "OFF", c["key"][:24], p["start"] - m["start"] if m else 0,
                                           p["end"] - m["end"] if m else 0, c["reason"][:90]))
missing_ref = {s["key"] for s in ref.get("skipped", [])}
missing_draft = {s["key"] for s in d["skipped"]}
print("\nwithin 30 s: %d of %d | not recorded - checked: %s, draft: %s" % (ok, len(mine), sorted(missing_ref), sorted(missing_draft)))
