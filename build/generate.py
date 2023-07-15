#!/usr/bin/env python3

import datetime
import re
import csv
import textwrap
import string
import yaml
import datetime
from datetime import timedelta

from jinja2 import Environment, FileSystemLoader
from jinja_markdown import MarkdownExtension


def generate_short_url(url):
    url = url.replace(" ", "-").replace("_", "-")
    url = ''.join(filter(lambda x: x in string.printable, url))
    url = re.sub('[^a-zA-Z0-9]', '-', url)
    url = re.sub('[-]+', '-', url)
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

# pick up the photos
for talk in talks_raw:
    photo = talk.get("photo")
    if photo:
        talk["photo_url"] = "./assets/images/profiles/" + photo
    else:
        talk["photo_url"] = talk.get("avatar")

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
id = 0
for talk in talks:
    talk["id"] = str(id)
    id += 1
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
    tracks[track] = prepend + tracks[track]
    for talk in tracks[track]:
        talk["start_time"] = current_time
        talk["duration"] = int(talk.get("duration") or DEFAULT_DURATION)
        current_time += timedelta(minutes=talk["duration"])

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

# SITEMAP
print(DIVIDER)
print("Generating sitemap.xml with %d items" % len(SITEMAP_URLS))
now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()
with open(BASE_FOLDER + "/sitemap.xml", "w") as f:
    template = env.get_template("sitemap.xml")
    f.write(template.render(urls=SITEMAP_URLS, now=now))
