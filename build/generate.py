#!/usr/bin/env python3

import datetime
import re
import csv
import textwrap
import string
import yaml
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
                item["abstract_s"] = textwrap.shorten(item.get("abstract",""), 200-len(item.get("title","")), placeholder="...")
                item["abstract_m"] = textwrap.shorten(item.get("abstract",""), 400-len(item.get("title","")), placeholder="...")
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
    talks = [
        talk
        for talk in read_csv("./_db/talks_2023.csv")
        if "confirmed" in talk["status"].lower()
    ]
    tracks_ordered = []
    tracks = dict()
    for talk in talks:
        track = talk.get("track")
        if track not in tracks:
            tracks[track] = []
            tracks_ordered.append(track)
        tracks[track].append(talk)
    context["talks"] = talks
    context["tracks"] = tracks_ordered
    context["talks_by_tracks"] = tracks
    print("Loaded %d confirmed talks in %d tracks: %s" % (len(context["talks"]), len(tracks), tracks.keys()))

except Exception as e:
    print("Couldn't read talks", e)


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
