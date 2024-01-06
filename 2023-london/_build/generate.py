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
    with open(path, 'r') as f:
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
    talks_raw = read_csv("./_db/talks_2023.csv")
except Exception as e:
    print("Couldn't read talks", e)

# pick up the ids & photos
for i, talk in enumerate(talks_raw):
    talk["id"] = str(i)
    photo = talk.get("photo")
    if photo:
        talk["photo_url"] = "./assets/images/profiles/" + photo
    else:
        talk["photo_url"] = talk.get("avatar")
    print(generate_talk_url(talk))
    talk["short_url"] = generate_talk_url(talk)

# sort into talks and keynotes
talks = [
    talk
    for talk in talks_raw
    if "confirmed" in talk["status"].lower()
]
context["talks"] = talks
keynotes = [
    talk
    for talk in talks_raw
    if "keynote" in talk["status"].lower()
]
context["keynotes"] = keynotes

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
    with open(BASE_FOLDER + "/" + page, "w") as f:
        print("Writing out", page)
        template = env.get_template(page)
        f.write(template.render(page=page, **context))
        if page != "index.html":
            SITEMAP_URLS.append((page.replace(".html",""), 0.75))

# template each talk page for the event
for talk in talks_raw:
    print("Generating talk subpage %s" % (talk.get("short_url")))
    with open(BASE_FOLDER + "/" + talk.get("short_url").replace(".html","")  + ".html", "w") as f:
        template = env.get_template("talk.html")
        f.write(template.render(talk=talk, **context))
        SITEMAP_URLS.append((talk.get("short_url").replace(".html",""), 0.75))

# SITEMAP
print(DIVIDER)
print("Generating sitemap.xml with %d items" % len(SITEMAP_URLS))
now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()
with open(BASE_FOLDER + "/sitemap.xml", "w") as f:
    template = env.get_template("sitemap.xml")
    f.write(template.render(urls=SITEMAP_URLS, now=now))
