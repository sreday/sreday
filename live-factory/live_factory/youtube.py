"""YouTube-ready blocks for <event>/YouTube items.txt, after the Conf42 factory's youtube.py:

    <short title> | <Speaker(s)> | SREday London 2026 Q1          (<= title_max characters)

    SREday London 2026 Q1 - March 12, 2026
    Grab your ticket for the next SREday: https://www.sreday.com
    Upcoming SREday CFPs: https://cfp.ninja/?q=sreday&status=open

    Chapters
    00:00 ...

plus, per talk, the URL of its ready-made thumbnail (the website draws them: /<event>/teasers/<speaker>.png).
The short title is written by Descript's AI editor in the chapters job (or by hand in state.json,
talks.<key>.yt_title); shorten_title() is only the fallback. The description lines live in settings.yml (youtube:).
"""
import re
import urllib.parse


def clean_title(t):
    return re.sub(r"\s+", " ", str(t)).strip()


def suffix(ev, speakers):
    return " | %s | %s" % (speakers, ev["title"])


def budget(ev, cfg, speakers):
    limit = int((cfg.get("youtube") or {}).get("title_max") or 100)
    return limit - len(suffix(ev, speakers))


def speakers_of(talk):
    """The names for the title: the sheet's name cell without a ' - ClickHouse Meetup' style tail."""
    return re.split(r"\s+-\s+", talk["name"])[0].strip().lstrip("_")


def shorten_title(title, room):
    """Fallback when nobody wrote a short title: drop trailing 'for/in/with ...' phrases, then the subtitle after a
    colon, then cut at a word boundary."""
    t = clean_title(title)
    while len(t) > room:
        m = re.match(r"^(.*\S)\s+(for|in|with|at|on|using|across|through|via)\s+[^:]+$", t, re.I)
        if m and len(m.group(1)) >= 20:
            t = m.group(1).rstrip(",;:-")
            continue
        break
    if len(t) > room and ":" in t and len(t.split(":")[0]) >= 12:
        t = t.split(":")[0].strip()
    if len(t) > room:
        t = t[:room].rsplit(" ", 1)[0].rstrip(",;:-&")
    return t


def title_for(ev, cfg, talk, written=None):
    who = speakers_of(talk)
    room = budget(ev, cfg, who)
    if room < 20:                                   # many speakers: name the first one
        who = re.split(r",|&", who)[0].strip() + " et al."
        room = budget(ev, cfg, who)
    t = clean_title(written or "") if written else ""
    if not t or len(t) > room:
        t = talk["title"] if len(talk["title"]) <= room else shorten_title(talk["title"], room)
    return t + suffix(ev, who)


def thumbnail_url(ev, talk):
    return "https://%s/%s/teasers/%s" % (ev["domain"], ev["folder"], urllib.parse.quote(talk["thumb"])) if ev["domain"] else ""


def block(ev, cfg, talk, chapters, written=None):
    """(title, description lines) in the channels' house style (89 published SREday / LLMday talks, 2026-10-16):

        SREday Austin 2026 Q2 - May 11, 2026
        Grab your ticket for the next SREday: https://www.sreday.com
        Upcoming SREday CFPs: https://cfp.ninja/?q=sreday&status=open

        Chapters
        00:00 Welcome and Intro
        ..."""
    yt = cfg.get("youtube") or {}
    values = {"event": ev["title"], "date": ev.get("date_string", ""), "brand": ev["brand_name"],
              "brand_key": ev["brand"], "www_url": ev.get("www_url", ""),
              "talk_url": ("%s%s.html" % (ev["event_url"], talk["page"])) if ev["event_url"] else "",
              "event_url": ev["event_url"], "site_url": ev["site_url"], "cfp_url": ev["cfp_url"],
              "channel_url": ev["channel_url"]}
    lines = []
    for tpl in yt.get("description") or []:
        needed = re.findall(r"\{(\w+)\}", tpl)
        if all(values.get(n) for n in needed):
            lines.append(tpl.format(**values))
    if chapters:
        lines += ["", "Chapters"] + ["%s %s" % (c["at"], c["title"]) for c in chapters]
    return title_for(ev, cfg, talk, written), lines
