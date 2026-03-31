#!/usr/bin/env python3

import datetime
import re
import csv
import textwrap
import string
import yaml
import datetime
from datetime import timedelta
import markdown

from jinja2 import Environment, FileSystemLoader
from jinja_markdown import MarkdownExtension


def generate_short_url(url):
    url = url.replace(" ", "-").replace("_", "-")
    url = ''.join(filter(lambda x: x in string.printable, url))
    url = re.sub('[^a-zA-Z0-9]', '-', url)
    url = re.sub('[-]+', '-', url)
    return url[:100]

def generate_talk_url(talk):
    url = "{name1}{name2}{company}{title}".format(
        name1=talk.get("name", "").replace(" ", "_"),
        name2=("_" + talk.get("co-speaker", "").replace(" ", "_")) if talk.get("co-speaker") else "",
        company=("_" + talk.get("organization", "").replace(" ", "_")) if talk.get("organization") else "",
        title=("_" + talk.get("title", "").replace(",", "_").replace(" ", "_")) if talk.get("title") else "",
    )
    url = ''.join(filter(lambda x: x in string.printable, url))
    url = re.sub('[\W]+', '', url)
    return url[:100]

def read_csv(path):
    """ Read the pre-process the CSV """
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for item in reader:
            item = dict(item)
            if "abstract" in item:
                item["abstract_s"] = textwrap.shorten(item.get("abstract",""), 300, placeholder="...")
                item["abstract_m"] = textwrap.shorten(item.get("abstract",""), 1000, placeholder="...")
            items.append(item)
    return items


DIVIDER = "#"*80
SITEMAP_URLS = []

# init the jinja stuff
file_loader = FileSystemLoader("_templates")
env = Environment(loader=file_loader)
env.add_extension(MarkdownExtension)
env.filters["short_url"] = generate_short_url
env.filters["markdown"] = lambda x: markdown.markdown(x)

# load the context from the metadata file
print(DIVIDER)
print("Loading context")
with open('metadata.yml') as f:
    context = yaml.load(f, Loader=yaml.FullLoader)
    BASE_FOLDER = "./" + context.get("base_folder")


try:
    # read the csv
    talks_raw = read_csv("./_db/talks.csv")
except Exception as e:
    print("Couldn't read talks", e)

# pick up the ids & photos
for i, talk in enumerate(talks_raw):
    talk["id"] = str(i)
    photo = talk.get("photo")
    if photo:
        talk["photo_url"] = "../speakers/" + photo
    else:
        talk["photo_url"] = talk.get("avatar")
    print(generate_talk_url(talk))
    talk["short_url"] = generate_talk_url(talk)

# sort into talks and keynotes
talks = [
    talk
    for talk in talks_raw
    if "talk" in talk["status"].lower()
]
context["talks"] = talks
keynotes = [
    talk
    for talk in talks_raw
    if "keynote" in talk["status"].lower()
]
context["keynotes"] = keynotes
mcs = [
    talk
    for talk in talks_raw
    if "mc" in talk["status"].lower()
]
context["mcs"] = mcs

# sort by track
tracks_ordered = []
tracks = dict()
for talk in talks:
    track = talk.get("track")
    if track not in tracks:
        tracks[track] = []
        tracks_ordered.append(track)
    tracks[track].append(talk)
context["tracks"] = tracks_ordered

# generate times
def start_time():
    return datetime.datetime(
        hour=9,
        minute=0,
        year=2023,
        month=9,
        day=14,
    )
def close_time():
    return datetime.datetime(
        hour=17,
        minute=0,
        year=2023,
        month=9,
        day=14,
    )

# insert breaks
for track in tracks_ordered:
    coffee = dict(
        title="Coffee break",
        duration=30,
        comment="Main lobby",
    )
    lunch = dict(
        title="Lunch & networking",
        duration=60,
        comment="Main lobby",
    )
    closing = dict(
        title="Networking & sponsor crawl",
        duration=30,
        comment="Main lobby",
    )
    talks = tracks[track]
    tracks[track] = [coffee] + talks[:4] + [lunch] + talks[4:] + [closing]

# prepend the first track each day with the keynotes
# offset other tracks with that duration
DEFAULT_DURATION = 30
for i, track in enumerate(tracks_ordered):
    current_day = (i // 3) + 1
    current_time = start_time()
    prepend = []
    for talk in keynotes:
        if talk.get("day") == str(current_day):
            talk["start_time"] = current_time
            talk["duration"] = int(talk.get("duration") or DEFAULT_DURATION)
            current_time += timedelta(minutes=talk["duration"])
            # for the first track of the day, actually prepend
            if i == 0 or i == 3:
                prepend.append(talk)
    for talk in tracks[track]:
        talk["start_time"] = current_time
        talk["duration"] = int(talk.get("duration") or DEFAULT_DURATION)
        current_time += timedelta(minutes=talk["duration"])
    close = dict(
        title="Venue closes & pub",
        start_time=close_time(),
    )
    tracks[track] = prepend + tracks[track] + [close]

context["talks_by_tracks"] = tracks
print("Loaded %d confirmed talks in %d tracks: %s" % (len(context["talks"]), len(tracks), tracks.keys()))



# MAIN PAGES
print(DIVIDER)
pages = ["index.html"]
print(f"Generating main pages: {pages}")
for page in pages:
    with open(BASE_FOLDER + "/" + page, "w", encoding="utf-8") as f:
        print("Writing out", page)
        template = env.get_template(page)
        f.write(template.render(page=page, **context))
        if page != "index.html":
            SITEMAP_URLS.append((page.replace(".html",""), 0.75))

# template each talk page for the event
for talk in talks_raw:
    print("Generating talk subpage %s" % (talk.get("short_url")))
    with open(BASE_FOLDER + "/" + talk.get("short_url").replace(".html","")  + ".html", "w", encoding="utf-8") as f:
        template = env.get_template("talk.html")
        f.write(template.render(talk=talk, **context))
        SITEMAP_URLS.append((talk.get("short_url").replace(".html",""), 0.75))


# ── SPONSORSHIP PAGE ─────────────────────────────────────────────────────────
import os as _os
import glob as _glob

print(DIVIDER)
print("Generating sponsorship.html")

_sponsorship_config = {}
_sponsorship_yaml = '../sponsorship.yaml'
if _os.path.exists(_sponsorship_yaml):
    with open(_sponsorship_yaml) as _f:
        _sponsorship_config = yaml.load(_f, Loader=yaml.FullLoader)

_current_folder = _os.path.basename(_os.getcwd())
_parts = _current_folder.split('-')
_city_parts = [p for p in _parts
               if not re.match(r'^\d{4}$', p)
               and not re.match(r'^q\d+$', p, re.IGNORECASE)]
_city_slug = '-'.join(_city_parts)

_all_siblings = sorted(_glob.glob('../20*/'))
_same_city = [
    f for f in _all_siblings
    if _city_slug in _os.path.basename(_os.path.normpath(f))
    and _os.path.basename(_os.path.normpath(f)) != _current_folder
]

_past_keynotes_raw, _all_past_speakers, _past_sponsors_raw = [], [], []
for _folder in _same_city:
    _talks_path = _os.path.join(_folder, '_db', 'talks.csv')
    if _os.path.exists(_talks_path):
        for _t in read_csv(_talks_path):
            _status = _t.get('status', '').lower()
            if 'keynote' in _status:
                _past_keynotes_raw.append(_t)
            if 'keynote' in _status or 'confirmed' in _status:
                _all_past_speakers.append(_t)
    _meta_path = _os.path.join(_folder, 'metadata.yml')
    if _os.path.exists(_meta_path):
        with open(_meta_path) as _f:
            _pm = yaml.load(_f, Loader=yaml.FullLoader)
        _past_sponsors_raw.extend(_pm.get('sponsors', []) or [])

_seen_names = set()
_past_keynotes = []
for _t in _past_keynotes_raw:
    _name = _t.get('name', '').strip()
    if _name and _name not in _seen_names:
        _seen_names.add(_name)
        _wparts = _name.split()
        _initials = ''.join(p[0].upper() for p in _wparts[:2])
        _past_keynotes.append({
            'name': _name,
            'organization': _t.get('organization', ''),
            'initials': _initials,
        })
_past_keynotes.sort(key=lambda x: x['name'])

_company_counts = {}
for _t in _all_past_speakers:
    _org = _t.get('organization', '').strip()
    if _org:
        _company_counts[_org] = _company_counts.get(_org, 0) + 1
_top_companies = sorted(_company_counts.items(), key=lambda x: x[1], reverse=True)[:5]

_seen_logos = set()
_past_sponsors = []
for _s in _past_sponsors_raw:
    _logo = _s.get('logo', '').strip()
    if _logo and _logo not in _seen_logos:
        _seen_logos.add(_logo)
        _sname = re.sub(r'[-_]', ' ', _os.path.splitext(_logo)[0]).title()
        _past_sponsors.append({'logo': _logo, 'url': _s.get('url', ''), 'name': _sname})

_talk_count = len(talks) + len(keynotes)
_past_editions = len(_same_city)
_event_size = context.get('event_size', 'medium')
_sponsorship_tiers = _sponsorship_config.get('tiers', [])
_additional_options = _sponsorship_config.get('additional_options', [])

print(f"  City slug: {_city_slug}")
print(f"  Past editions: {_past_editions}")
print(f"  Past keynotes: {len(_past_keynotes)}, sponsors: {len(_past_sponsors)}, top cos: {len(_top_companies)}")

_sp_template = env.get_template('sponsorship.html')
with open(BASE_FOLDER + '/sponsorship.html', 'w', encoding='utf-8') as _f:
    _f.write(_sp_template.render(
        page='sponsorship.html',
        noindex=True,
        past_keynotes=_past_keynotes,
        top_companies=_top_companies,
        past_sponsors=_past_sponsors,
        past_editions=_past_editions,
        talk_count=_talk_count,
        sponsorship_tiers=_sponsorship_tiers,
        additional_options=_additional_options,
        **context
    ))
print("Done: sponsorship.html")
# ── END SPONSORSHIP PAGE ─────────────────────────────────────────────────────

# SITEMAP
print(DIVIDER)
print("Generating sitemap.xml with %d items" % len(SITEMAP_URLS))
now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()
with open(BASE_FOLDER + "/sitemap.xml", "w", encoding="utf-8") as f:
    template = env.get_template("sitemap.xml")
    f.write(template.render(urls=SITEMAP_URLS, now=now))
