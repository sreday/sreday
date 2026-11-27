"""Proofread a talk's transcript before it goes to YouTube as captions (Marek 2026-11-27: "download the descript
transcript, proofread it in the context of the talk to catch and fix obvious problems, and upload to youtube videos
as captions"). Ported from the Conf42 factory's proofread.py (2026-10-29), which has proofread ~1,600 transcripts.

Descript's transcripts are good but know nothing about software engineering or the conference: "sorry day" for
SREday, "L LMS", "Cooper Netties", a speaker's name spelled three ways. After the download from Descript:

1. glossary.yml - sure-fire fixes (regex -> text), free and instant;
2. Claude reads the whole transcript with the talk's title, abstract, speakers and company, and returns only the words
   that are clearly misheard ({cue, from, to}). Run through the Claude Code CLI (`claude -p`, no tools), so it needs
   no API key - the machine's Claude login (Marek's subscription) is enough. No CLI or a failed call: the glossary
   fixes stand and the talk is marked "partial"; the next pass tries Claude again before the captions go up.

Only the text of a cue changes - never its timing, its number or the number of cues - and a fix whose "from" is not
in its cue is dropped, so Claude can't rewrite the talk. Every fix is listed in srt/proofread/<file>.json.
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile

import yaml

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # live-factory/
TIME = re.compile(r"^\d+:\d+:\d+[,.]\d+\s*-->")

PROMPT = """You proofread an automatic transcript of a conference talk (speech-to-text by Descript) before it is \
published as the talk's subtitles on YouTube.

The speech recogniser knows nothing about software engineering, so it mishears technical terms, product, project and \
company names, acronyms, the conference's name and the speakers' names. Fix ONLY such mistranscriptions: words that \
make no sense in the context of this talk and are clearly a mishearing of what the speaker said. Examples: "Cooper \
Netties" -> "Kubernetes"; "L LMS" -> "LLMs"; "get ops" -> "GitOps"; "sorry day" / "SRE day" -> "SREday"; a speaker's \
or company's name spelled differently from the talk info below.

The conference is "{brand}"; this event is "{event}". When the speaker names this conference and it was misheard, \
spell it "{brand}". But what the speaker says, goes: another conference or city they name stays as they said it.

Do NOT rephrase, reorder words, fix the speaker's grammar, remove fillers or repetitions, change punctuation or style, \
translate, or touch anything you are not sure about. Never add words the speaker didn't say (no middle names from the \
talk info, no completed sentences), and leave ordinary English words that make sense where they stand. Fix a word \
only when you know what was said - no guesses. When in doubt, leave it. Most cues need nothing; an empty list is a \
fine answer.

Return every fix as {{"cue": <number>, "from": "...", "to": "..."}}: cue = the number at the start of the line; from = \
the wrong words copied exactly, character for character, from that cue (as few words as needed to be unique in it); \
to = what the speaker said. A mistake running into the next cue gets one fix per cue.

Talk info:
{info}

Transcript (one cue per line, "<cue>| <text>"):
{cues}
"""

SCHEMA = {"type": "object", "required": ["fixes"], "properties": {"fixes": {"type": "array", "items": {
    "type": "object", "required": ["cue", "from", "to"],
    "properties": {"cue": {"type": "integer"}, "from": {"type": "string"}, "to": {"type": "string"}}}}}}


# ---- SRT in and out: only the text lines of a cue are ever touched ----------------------------------------------
def parse(text):
    """[{"head": [number, time], "lines": [...]}] - blocks that don't look like cues are kept as they are."""
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip("\n")):
        lines = block.split("\n")
        if len(lines) >= 2 and lines[0].strip().isdigit() and TIME.match(lines[1].strip()):
            out.append({"head": lines[:2], "lines": lines[2:]})
        else:
            out.append({"head": lines, "lines": [], "raw": True})
    return out


def render(cues, like):
    """Back to SRT, with the original's line endings and its blank line(s) at the end."""
    body = like.replace("\r\n", "\n")
    s = "\n\n".join("\n".join(c["head"] + c["lines"]) for c in cues) + (body[len(body.rstrip("\n")):] or "\n")
    return s.replace("\n", "\r\n") if "\r\n" in like else s


def _replace(lines, frm, to, flags=0):
    """Replace frm (any whitespace, line breaks included, between its words) in the cue's lines with to, keeping the
    cue's line breaks where they were. None when frm is not there."""
    text = "\n".join(lines)
    words = frm.split()
    if not words:
        return None
    m = re.search(r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)", text, flags) or \
        re.search(r"\s+".join(map(re.escape, words)), text, flags)
    if not m:
        return None
    seg = m.group(0)
    new = to
    if "\n" in seg:                                     # keep the cue's line break
        before, tw = len(seg[:seg.index("\n")].split()), to.split()
        at = len(tw)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [w.lower() for w in words],
                                                           [w.lower() for w in tw]).get_opcodes():
            if i1 <= before <= i2:
                at = j1 + (before - i1 if tag == "equal" else round((before - i1) * (j2 - j1) / max(1, i2 - i1)))
                break
        new = " ".join(tw[:at]) + "\n" + " ".join(tw[at:]) if 0 < at < len(tw) else to
    return (text[:m.start()] + new + text[m.end():]).split("\n")


def _keep_case(found, to):
    """'Future Flags' -> 'Feature Flags', 'future flag' -> 'feature flag'; a replacement with capitals of its own
    (SREday, LLMs) is written as it is."""
    if to != to.lower():
        return to
    if found[:1].isupper():
        if all(w[:1].isupper() for w in found.split()):
            return " ".join(w[:1].upper() + w[1:] for w in to.split(" "))
        return to[:1].upper() + to[1:]
    return to


# ---- 1. the glossary -------------------------------------------------------------------------------------------
def glossary(brand=""):
    with open(os.path.join(HERE, "glossary.yml"), encoding="utf-8") as f:
        rules = (yaml.safe_load(f) or {}).get("fixes") or []
    return [(re.compile(r["from"], re.I), r["to"]) for r in rules
            if not r.get("brands") or re.search(r["brands"], brand or "", re.I)]


def glossary_pass(cues, brand):
    fixes = []
    for rx, to in glossary(brand):
        for i, c in enumerate(cues):
            if c.get("raw"):
                continue
            text = "\n".join(c["lines"])
            new = rx.sub(lambda m: _keep_case(m.group(0), to), text)
            if new != text:
                for m in rx.finditer(text):
                    if m.group(0) != _keep_case(m.group(0), to):
                        fixes.append({"cue": i + 1, "from": m.group(0).replace("\n", " "),
                                      "to": _keep_case(m.group(0), to), "by": "glossary"})
                c["lines"] = new.split("\n")
    return fixes


# ---- 2. Claude ---------------------------------------------------------------------------------------------------
def claude_cli():
    found = shutil.which("claude")
    if found:
        return found
    for p in (os.path.expanduser("~/.local/bin/claude"), os.path.expanduser("~/.local/bin/claude.exe"),
              os.path.expanduser("~/.claude/local/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if os.path.isfile(p):                           # a launcher run from Finder / a scheduled task has a short PATH
            return p
    return None


def talk_info(ctx):
    rows = [("Event", ctx.get("event")), ("Talk title", ctx.get("title")), ("Speakers", ctx.get("speakers")),
            ("Company", ctx.get("companies")), ("Abstract", ctx.get("abstract"))]
    return "\n".join("- %s: %s" % (k, " ".join(str(v).split())) for k, v in rows if v)


def claude_pass(cues, ctx, cfg, run_cli=None):
    """Ask Claude for the misheard words; apply the ones found in their cue. Returns (fixes, cost_usd)."""
    numbered = "\n".join("%d| %s" % (i + 1, " ".join(" ".join(c["lines"]).split()))
                         for i, c in enumerate(cues) if not c.get("raw"))
    prompt = PROMPT.format(brand=ctx.get("brand") or "the conference", event=ctx.get("event", ""),
                           info=talk_info(ctx), cues=numbered)
    out = (run_cli or _run_cli)(prompt, cfg)
    if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        raise RuntimeError("Claude CLI: %s" % str(out.get("result") or out.get("subtype") or "no fixes returned")[-300:])
    fixes = []
    for fx in out["structured_output"].get("fixes") or []:
        n, frm, to = fx.get("cue"), (fx.get("from") or "").strip(), (fx.get("to") or "").strip()
        if not isinstance(n, int) or not 1 <= n <= len(cues) or cues[n - 1].get("raw") or not frm or frm == to:
            continue
        if len(frm) > 160 or len(to) > len(frm) + 60:   # a fix, not a rewrite
            continue
        new = _replace(cues[n - 1]["lines"], frm, to)
        if new is None:
            continue
        cues[n - 1]["lines"] = new
        fixes.append({"cue": n, "from": frm, "to": to, "by": "claude"})
    return fixes, out.get("total_cost_usd")


def _run_cli(prompt, cfg):
    exe = claude_cli()
    if not exe:
        raise RuntimeError("no Claude Code CLI on this machine (claude not found)")
    cmd = [exe, "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--output-format", "json",
           "--json-schema", json.dumps(SCHEMA)]
    if cfg.get("proofread_model"):
        cmd += ["--model", str(cfg["proofread_model"])]
    with tempfile.TemporaryDirectory() as tmp:          # an empty folder: no CLAUDE.md or project settings read
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=tmp,
                           timeout=int(cfg.get("proofread_timeout_seconds", 900)))
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise RuntimeError("Claude CLI: %s" % (r.stderr or r.stdout or "no answer").strip()[-300:])


# ---- one file --------------------------------------------------------------------------------------------------
def proofread_file(path, ctx, cfg, use_claude=True, run_cli=None):
    """Fix the transcript at path in place. Returns {"fixes": [...], "glossary": n, "claude": n, "partial": bool,
    "error": str|None, "cost_usd": x}. The file is rewritten only when something changed."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        raw = f.read()
    cues = parse(raw)
    rep = {"file": os.path.basename(path), "fixes": glossary_pass(cues, ctx.get("brand_key", "")), "partial": False,
           "error": None, "cost_usd": None}
    if use_claude and str(cfg.get("proofread_claude", True)).lower() not in ("false", "0", "no"):
        try:
            fixes, rep["cost_usd"] = claude_pass(cues, ctx, cfg, run_cli)
            rep["fixes"] += fixes
        except Exception as ex:                          # the glossary fixes stand; Claude is tried again later
            rep["partial"], rep["error"] = True, str(ex)[:300]
    else:
        rep["partial"] = True
    rep["glossary"] = sum(1 for x in rep["fixes"] if x["by"] == "glossary")
    rep["claude"] = sum(1 for x in rep["fixes"] if x["by"] == "claude")
    new = render(cues, raw)
    if new != raw.replace("﻿", ""):
        with open(path + ".part", "w", encoding="utf-8", newline="") as f:
            f.write(new)
        os.replace(path + ".part", path)
    return rep


def talk_context(ev, talk):
    """What Claude gets to know about the talk: the events.talks() row and the event."""
    ctx = {"event": ev["title"], "brand": ev.get("brand_name"), "brand_key": ev.get("brand")}
    if talk:
        ctx.update(title=talk.get("title"), speakers=talk.get("name"), companies=talk.get("organization"),
                   abstract=talk.get("abstract"))
    return ctx
