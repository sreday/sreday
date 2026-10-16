"""Events of every brand (SREday, LLMday, PLATFORMday, PEC, ...), read-only from the brand site repos:
<repo>/<folder>/metadata.yml and <repo>/<folder>/_db/talks.csv (a Drive export - never written to).

An event is named "<brand>-<folder>", e.g. sreday-2026-london-q1 (as on cfp.ninja).

schedule() rebuilds each track's running order and times the way the site generator does
(_event_template/_build/generate.py, "LONGER SESSIONS" to "synchronize break times across tracks"): talks in sheet
order per track, breaks after `talks_before` sheet rows, keynotes at the start of their day in the first room,
default 30 minutes. Keep it in step with the generator by hand - it is a script, not importable.
"""
import csv
import datetime
import os
import re
import string

import yaml

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # live-factory/
REPO = os.path.dirname(HERE)                                            # the website repo (SREday)
DEFAULT_TALK_DURATION = 30
LIVE_KINDS = ("talk", "keynote", "workshop")
_KEYNOTE_PREFIX = re.compile(r"^\s*keynote\s*:\s*", re.I)
_WORKSHOP_PREFIX = re.compile(r"^\s*(?:\d+\s*h\s+)?workshop\s*:\s*", re.I)


# ---- brands and events ------------------------------------------------------------------------------------------
def brands(cfg):
    return cfg.get("brands") or {"sreday": {"repo": "website"}}


def github_root(cfg):
    root = cfg.get("github_root")
    return os.path.expanduser(str(root)) if root else os.path.dirname(REPO)


def brand_repo(cfg, brand):
    b = brands(cfg).get(brand)
    if not b:
        raise KeyError("unknown brand %r - the brands in settings.yml: %s" % (brand, ", ".join(sorted(brands(cfg)))))
    return os.path.join(github_root(cfg), b["repo"])


def split_id(event_id):
    """'sreday-2026-london-q1' -> ('sreday', '2026-london-q1')."""
    m = re.match(r"^([a-z0-9]+?)-((?:19|20)\d\d-.+)$", event_id.strip().strip("/"))
    if not m:
        raise ValueError("event names look like sreday-2026-london-q1 (brand-folder), not %r" % event_id)
    return m.group(1), m.group(2)


def all_events(cfg):
    """Every event folder of every brand whose repo is on this machine: [(event_id, folder path)]."""
    out = []
    for brand in sorted(brands(cfg)):
        repo = brand_repo(cfg, brand)
        if not os.path.isdir(repo):
            continue
        for name in sorted(os.listdir(repo)):
            if re.match(r"^20\d\d-", name) and os.path.exists(os.path.join(repo, name, "metadata.yml")):
                out.append(("%s-%s" % (brand, name), os.path.join(repo, name)))
    return out


def event(cfg, event_id):
    brand, folder = split_id(event_id)
    repo = brand_repo(cfg, brand)
    path = os.path.join(repo, folder)
    if not os.path.exists(os.path.join(path, "metadata.yml")):
        raise KeyError("no event %s: %s has no metadata.yml" % (event_id, path))
    with open(os.path.join(path, "metadata.yml"), encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}
    m = re.match(r"^(\d{4})-(.+?)(?:-(q\d))?$", folder)
    year, slug_city, quarter = m.group(1), m.group(2), (m.group(3) or "").upper()
    city = str(meta.get("city_name") or slug_city.replace("-", " ").title())
    brand_name = str(meta.get("brand_name") or brand)
    # as on the YouTube channels: "SREday Austin 2026 Q2", always with the quarter
    title = " ".join(p for p in [brand_name, city, year, quarter] if p)
    domain = str(meta.get("brand_domain") or "")
    start = str(meta.get("start_time") or "")
    return {"id": event_id, "brand": brand, "folder": folder, "path": path, "brand_name": brand_name, "city": city,
            "year": year, "quarter": quarter, "title": title, "start_time": start, "date": start[:10],
            "days": int(meta.get("days") or 1), "rooms": list(meta.get("rooms") or ["main"]),
            "breaks": list(meta.get("breaks") or []), "domain": domain,
            "site_url": ("https://%s/" % domain) if domain else "",
            "www_url": ("https://www.%s" % domain) if domain else "",
            "date_string": str(meta.get("date_string") or start[:10]),
            "event_url": ("https://%s/%s/" % (domain, folder)) if domain else "",
            "cfp_url": str(meta.get("cfp_url") or ""), "channel_url": str(meta.get("youtube_url") or ""),
            "csv": os.path.join(path, "_db", "talks.csv")}


# ---- talks (same rules as the site generator) ----------------------------------------------------------------------
def talk_kind(status):
    s = str(status or "").strip().lower()
    if re.search(r"\bdraft\b", s):
        return "draft"
    if "keynote" in s:
        return "keynote"
    if re.search(r"\bworkshop\b", s):
        return "workshop"
    if "confirmed" in s or re.search(r"\btalk\b", s):
        return "talk"
    return None


def title_display(row, kind):
    title = (row.get("title") or "").strip()
    if kind == "keynote" or _KEYNOTE_PREFIX.match(title):
        return _KEYNOTE_PREFIX.sub("", title)
    if kind == "workshop":
        return _WORKSHOP_PREFIX.sub("", title)
    return title


def talk_url(row):
    """generate.py generate_talk_url: the talk page's file name (without .html)."""
    url = "{name1}{name2}{company}{title}".format(
        name1=row.get("name", "").replace(" ", "_"),
        name2=("_" + row.get("co-speaker", "").replace(" ", "_")) if row.get("co-speaker") else "",
        company=("_" + row.get("organization", "").replace(" ", "_")) if row.get("organization") else "",
        title=("_" + row.get("title", "").replace(",", "_").replace(" ", "_")) if row.get("title") else "")
    url = "".join(filter(lambda x: x in string.printable, url))
    url = re.sub(r"[\W]+", "", url)
    return url[:100]


def rows(ev):
    with open(ev["csv"], encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _tracks(ev, all_rows):
    """Talk and workshop rows per track in sheet order ({track: [item]}, track order as in the sheet), with a
    longer session merged: consecutive rows of one track with the same speaker + title, not across a break, become
    their first row with the minutes added up (the rest get "merged": True)."""
    cuts, before = set(), 0
    for b in ev["breaks"]:
        before += int(b.get("talks_before") or 0)
        cuts.add(before)
    tracks, order = {}, []
    for i, r in enumerate(all_rows):
        kind = talk_kind(r.get("status"))
        if kind not in ("talk", "workshop"):
            continue
        tr = str(r.get("track") or "").strip()
        if tr not in tracks:
            tracks[tr] = []
            order.append(tr)
        minutes = int(r.get("duration")) if str(r.get("duration") or "").strip() else DEFAULT_TALK_DURATION
        tracks[tr].append({"row": i, "kind": kind, "name": (r.get("name") or "").strip(),
                           "title_raw": (r.get("title") or "").strip(), "minutes": minutes})
    for tr in order:
        lead = None
        for pos, item in enumerate(tracks[tr]):
            if lead is not None and pos not in cuts and item["name"] == lead["name"] and item["title_raw"] == lead["title_raw"]:
                item["merged"] = True
                lead["minutes"] += item["minutes"]
            else:
                lead = item
    return tracks, order


def talks(ev):
    """The talks on the schedule (talk / keynote / workshop rows; a longer session's extra rows left out), in sheet
    order, each with a unique key (the speaker names; a second talk by the same speakers gets " (2)") and the file
    name of its YouTube thumbnail (as generate.py names it next to the teasers page)."""
    all_rows = rows(ev)
    tracks, _ = _tracks(ev, all_rows)
    merged = {x["row"] for items in tracks.values() for x in items if x.get("merged")}
    out, seen, thumbs = [], {}, set()
    for i, r in enumerate(all_rows):
        kind = talk_kind(r.get("status"))
        if kind not in LIVE_KINDS or i in merged:
            continue
        name = (r.get("name") or "").strip()
        n = seen[name] = seen.get(name, 0) + 1
        key = name if n == 1 else "%s (%d)" % (name, n)
        base, k = re.sub(r'[\\/:*?"<>|]+', "", name).strip(), 2
        thumb = base + ".png"
        while thumb in thumbs:
            thumb, k = "%s-%d.png" % (base, k), k + 1
        thumbs.add(thumb)
        out.append({"key": key, "row": i, "name": name, "kind": kind, "title": title_display(r, kind),
                    "track": str(r.get("track") or "").strip(), "day": str(r.get("day") or "").strip(),
                    "organization": (r.get("organization") or "").strip(),
                    "abstract": (r.get("abstract") or "").strip(), "youtube": (r.get("YouTube") or "").strip(),
                    "page": talk_url(r), "thumb": thumb})
    return out


def schedule(ev):
    """{track: {"day": n, "room": name, "date": "YYYY-MM-DD", "items": [{kind, key, name, title, start, end,
    minutes}]}} - every track's day as the website shows it (kind talk / workshop / keynote / break). start and end
    are "HH:MM" local times. A keynote belongs to the first room of its day."""
    by_key = {t["row"]: t for t in talks(ev)}
    all_rows = rows(ev)
    rooms = ev["rooms"] or ["main"]
    keynotes = [(i, r) for i, r in enumerate(all_rows) if talk_kind(r.get("status")) == "keynote"]
    tracks, order = _tracks(ev, all_rows)
    out = {}
    start = datetime.datetime.fromisoformat(ev["start_time"]) if ev["start_time"] else datetime.datetime(2000, 1, 1, 9)
    for ti, tr in enumerate(order):
        day = ti // len(rooms) + 1
        seq, offset = [], 0
        rows_ = tracks[tr]
        for b in ev["breaks"]:
            for _ in range(int(b.get("talks_before") or 0)):
                if offset < len(rows_):
                    seq.append(rows_[offset])
                    offset += 1
            seq.append({"kind": "break", "name": "", "title_raw": str(b.get("title") or "Break"),
                        "minutes": int(b.get("duration") or 0)})
        seq += rows_[offset:]
        seq = [x for x in seq if not x.get("merged")]
        head = []
        for i, r in keynotes:
            if str(r.get("day") or "").strip() == str(day):
                m = int(r.get("duration")) if str(r.get("duration") or "").strip() else DEFAULT_TALK_DURATION
                if ti % len(rooms) == 0:
                    head.append({"row": i, "kind": "keynote", "name": (r.get("name") or "").strip(),
                                 "title_raw": (r.get("title") or "").strip(), "minutes": m})
                else:
                    head.append({"kind": "placeholder", "minutes": m})
        seq = head + seq
        clock = start
        for x in seq:
            x["start"] = clock
            clock += datetime.timedelta(minutes=x["minutes"])
        out[tr] = {"day": day, "room": rooms[ti % len(rooms)], "items": seq,
                   "date": (start.date() + datetime.timedelta(days=day - 1)).isoformat()}
    # breaks start together across the tracks (the latest one wins), everything after them moves
    for b in ev["breaks"]:
        title = str(b.get("title") or "Break")
        latest = max((x["start"] for v in out.values() for x in v["items"]
                      if x["kind"] == "break" and x["title_raw"] == title), default=None)
        if latest is None:
            continue
        for v in out.values():
            items = v["items"]
            for i, x in enumerate(items):
                if x["kind"] == "break" and x["title_raw"] == title:
                    clock = latest
                    for y in items[i:]:
                        y["start"] = clock
                        clock += datetime.timedelta(minutes=y["minutes"])
                    break
    for v in out.values():
        items = []
        for x in v["items"]:
            if x["kind"] == "placeholder":
                continue
            t = by_key.get(x.get("row"))
            end = x["start"] + datetime.timedelta(minutes=x["minutes"])
            items.append({"kind": x["kind"], "key": t["key"] if t else "", "name": x["name"],
                          "title": t["title"] if t else x["title_raw"], "start": x["start"].strftime("%H:%M"),
                          "end": end.strftime("%H:%M"), "minutes": x["minutes"]})
        v["items"] = items
    return out


def track_for(ev, track=None, day=None):
    """The schedule track a recording belongs to: by track id ("1"), room name ("screen 2") or, when the day has a
    single track, by the day alone. Raises with the choices when it is ambiguous."""
    sch = schedule(ev)
    if track:
        t = str(track).strip()
        if t in sch:
            return t
        hits = [k for k, v in sch.items() if v["room"].lower() == t.lower() and (not day or v["day"] == int(day))]
        if len(hits) == 1:
            return hits[0]
    cands = [k for k, v in sch.items() if not day or v["day"] == int(day)]
    if not track and len(cands) == 1:
        return cands[0]
    raise ValueError("which track is this recording? %s" % "; ".join(
        "--track %s (day %d, %s)" % (k, v["day"], v["room"]) for k, v in sch.items()))
