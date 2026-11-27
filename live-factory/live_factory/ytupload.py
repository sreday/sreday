"""YouTube upload for the LIVE factory (Marek 2026-11-27: "automate the rest of the process, up to the video upload,
changing all info, putting thumbnail, adding to the playlist, and scheduling (private to public) exactly 5 weeks
after the conference"). Ported from the Conf42 cloud factory (conf42 src/factory/factory/ytupload.py), which has
done it since 2026-11-13.

Per finished talk, once (the video id lands in <work folder>/youtube.json first; a later pass never uploads it again):
  1. videos.insert, resumable: the final -14 LUFS MP4, title + description from youtube.block() (the event line, the
     ticket and CFP links, the chapters), tags (brand, event, city), Science & Technology, not for kids. PRIVATE with
     publishAt = the event day + 35 days at 09:00 in the event city's time zone (release_at); an event whose release
     is already past goes up public.
  2. thumbnails.set: the talk's 1280x720 thumbnail the website draws (/<event>/teasers/<speaker>.png), as JPEG when
     the PNG is over YouTube's 2 MB.
  3. captions.insert: the talk's SRT from Descript (trimmed to the final video).
  4. playlistItems.insert into the event's playlist ("SREday Dallas 2026 Q4"; made public when it doesn't exist - the
     videos are private until the release, so nothing shows early), kept in the event page's order.
What could not be finished (the day's quota, YouTube refusing a thumbnail) is tried again on the next pass.

Quota (2026-11): uploads have their own bucket (100 a day); thumbnail 50, captions 400, playlist 50, a status read
1, of 10,000 units a day. Since 2026-11-02 unaudited projects' uploads are no longer forced private.

Auth: ~/.live-factory/youtube/<brand>.json = {"client_id", "client_secret", "refresh_token", "channel_id"} (this computer only,
outside every repo), made once per channel with `python -m live_factory youtube-login <brand> <client_secret.json>` by the
person, in the browser - picking the brand's channel on Google's account screen. Every pass checks that the token's
channel is the brand's (settings youtube.channels, and the @handle of the event's youtube_url) and refuses otherwise.
"""
import datetime
import io
import json
import os
import re
import time
import urllib.parse

import requests

from . import events, youtube

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class QuotaExceeded(RuntimeError):
    pass


def token_path(brand):
    """In the user's own folder, not in a checkout: it outlives worktrees and re-clones (~/.live-factory/youtube/)."""
    return os.path.join(os.path.expanduser("~"), ".live-factory", "youtube", "%s.json" % brand)


def has_token(brand):
    return os.path.exists(token_path(brand))


class YouTube:
    def __init__(self, creds):
        self.c = creds
        self.token, self.at = None, 0

    @classmethod
    def for_brand(cls, brand):
        p = token_path(brand)
        if not os.path.exists(p):
            raise LookupError("no YouTube sign-in for %s on this computer - the person runs once: python -m live_factory "
                              "youtube-login %s <client_secret.json>" % (brand, brand))
        with open(p, encoding="utf-8") as f:
            return cls(json.load(f))

    def auth(self):
        if self.token and time.time() - self.at < 45 * 60:
            return self.token
        r = requests.post(TOKEN_URL, data={"client_id": self.c["client_id"], "client_secret": self.c["client_secret"],
                                           "refresh_token": self.c["refresh_token"], "grant_type": "refresh_token"},
                          timeout=60)
        if r.status_code != 200:
            raise RuntimeError("YouTube sign-in failed (%s) - the refresh token may be revoked: run youtube-login again"
                               % r.text[:200])
        self.token, self.at = r.json()["access_token"], time.time()
        return self.token

    def call(self, method, url, **kw):
        r = None
        for attempt in range(5):
            h = dict(kw.pop("headers", {}) or {}, Authorization="Bearer " + self.auth())
            r = requests.request(method, url, headers=h, timeout=kw.pop("timeout", 120), **kw)
            if r.status_code in (500, 502, 503, 504):
                time.sleep(3 * 2 ** attempt)
                continue
            if r.status_code == 401 and attempt == 0:
                self.token = None
                continue
            return r
        return r

    def channel(self):
        r = self.call("GET", API + "/channels", params={"part": "id,snippet", "mine": "true"})
        if r.status_code != 200:
            raise RuntimeError("YouTube: cannot read the channel (%s)" % r.text[:200])
        items = r.json().get("items") or []
        if not items:
            raise RuntimeError("YouTube: the sign-in has no channel")
        return items[0]

    def check_channel(self, want_id=None, want_handle=None):
        """The signed-in channel must be exactly the brand's: its id (settings / the login) and, when the event names
        one, its @handle. Never upload elsewhere."""
        ch = self.channel()
        want_id = want_id or self.c.get("channel_id")
        handle = (ch["snippet"].get("customUrl") or "").lower().lstrip("@")
        if not want_id:
            raise RuntimeError("YouTube: no channel id for this brand - refusing to upload anywhere")
        if ch["id"] != want_id or (want_handle and handle and handle != want_handle.lower().lstrip("@")):
            raise RuntimeError("YouTube: signed in to %s (%s, @%s), not the brand's channel %s%s - nothing uploaded" % (
                ch["snippet"].get("title"), ch["id"], handle, want_id, " (@%s)" % want_handle if want_handle else ""))
        return ch

    def upload(self, path, body, note=None):
        """Resumable videos.insert; resumes from YouTube's offset after a dropped chunk. Returns the video id."""
        size = os.path.getsize(path)
        r = self.call("POST", UPLOAD + "/videos", params={"uploadType": "resumable", "part": "snippet,status"},
                      json=body, headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
        if r.status_code in (403, 429) and ("quota" in r.text.lower() or "uploadlimit" in r.text.lower()):
            raise QuotaExceeded(r.text[:200])
        if r.status_code != 200 or "Location" not in r.headers:
            raise RuntimeError("YouTube upload refused: HTTP %d %s" % (r.status_code, r.text[:300]))
        url, sent, chunk, fails = r.headers["Location"], 0, 32 << 20, 0
        with open(path, "rb") as f:
            while True:
                f.seek(sent)
                data = f.read(chunk)
                end = sent + len(data) - 1
                try:
                    r = requests.put(url, data=data, timeout=600, headers={"Authorization": "Bearer " + self.auth(),
                                     "Content-Range": "bytes %d-%d/%d" % (sent, end, size)})
                except requests.RequestException:
                    r = None
                if r is not None and r.status_code in (200, 201):
                    return r.json()["id"]
                if r is not None and r.status_code == 308:
                    rg = r.headers.get("Range")
                    sent = int(rg.split("-")[1]) + 1 if rg else 0
                    fails = 0
                    if note:
                        note("youtube: %s %.0f%%" % (os.path.basename(path), 100.0 * sent / size))
                    continue
                fails += 1
                if fails > 8:
                    raise RuntimeError("YouTube upload kept failing (%s)" % (r.text[:200] if r is not None else "network"))
                time.sleep(min(120, 5 * 2 ** fails))
                q = requests.put(url, timeout=60, headers={"Authorization": "Bearer " + self.auth(),
                                                           "Content-Range": "bytes */%d" % size})
                if q.status_code in (200, 201):
                    return q.json()["id"]
                rg = q.headers.get("Range") if q.status_code == 308 else None
                sent = int(rg.split("-")[1]) + 1 if rg else 0

    def thumbnail(self, video_id, data, mime):
        r = self.call("POST", UPLOAD + "/thumbnails/set", params={"videoId": video_id, "uploadType": "media"},
                      data=data, headers={"Content-Type": mime})
        return r.status_code == 200, r.text[:200]

    def captions(self, video_id, srt_text, lang="en"):
        meta = {"snippet": {"videoId": video_id, "language": lang, "name": "English", "isDraft": False}}
        boundary = "livefactory"
        body = ("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n--%s\r\nContent-Type: "
                "application/octet-stream\r\n\r\n" % (boundary, json.dumps(meta), boundary)).encode() + \
            srt_text.encode("utf-8") + ("\r\n--%s--" % boundary).encode()
        r = self.call("POST", UPLOAD + "/captions", params={"part": "snippet", "uploadType": "multipart"}, data=body,
                      headers={"Content-Type": "multipart/related; boundary=" + boundary})
        return r.status_code == 200, r.text[:200]

    def playlist(self, title):
        """The channel's playlist with this exact title (case, punctuation and spaces ignored), else made public.
        Returns (id, made)."""
        want, page = _norm(title), None
        while True:
            params = {"part": "id,snippet", "mine": "true", "maxResults": 50}
            if page:
                params["pageToken"] = page
            r = self.call("GET", API + "/playlists", params=params)
            if r.status_code != 200:
                raise RuntimeError("playlists: %s" % r.text[:200])
            d = r.json()
            for p in d.get("items", []):
                if _norm(p["snippet"]["title"]) == want:
                    return p["id"], False
            page = d.get("nextPageToken")
            if not page:
                break
        r = self.call("POST", API + "/playlists", params={"part": "snippet,status"},
                      json={"snippet": {"title": title}, "status": {"privacyStatus": "public"}})
        if r.status_code != 200:
            raise RuntimeError("playlist: %s" % r.text[:200])
        return r.json()["id"], True

    def add_to_playlist(self, playlist_id, video_id):
        r = self.call("POST", API + "/playlistItems", params={"part": "snippet"},
                      json={"snippet": {"playlistId": playlist_id,
                                        "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
        return r.status_code == 200, r.text[:200]

    def playlist_items(self, playlist_id):
        """[(item id, video id, added at)] in the playlist's position order."""
        out, page = [], None
        while True:
            params = {"part": "snippet", "playlistId": playlist_id, "maxResults": 50}
            if page:
                params["pageToken"] = page
            r = self.call("GET", API + "/playlistItems", params=params)
            if r.status_code != 200:
                raise RuntimeError("playlist items: %s" % r.text[:200])
            d = r.json()
            out += [(i["id"], i["snippet"]["resourceId"].get("videoId"), i["snippet"].get("publishedAt") or "")
                    for i in d.get("items", [])]
            page = d.get("nextPageToken")
            if not page:
                return out

    def remove_item(self, item_id):
        r = self.call("DELETE", API + "/playlistItems", params={"id": item_id})
        return r.status_code in (200, 204), r.text[:200]

    def status(self, video_id):
        r = self.call("GET", API + "/videos", params={"part": "status,snippet,processingDetails", "id": video_id})
        items = (r.json().get("items") or []) if r.status_code == 200 else []
        return items[0] if items else None

    def delete(self, video_id):
        r = self.call("DELETE", API + "/videos", params={"id": video_id})
        return r.status_code in (200, 204), r.text[:200]


# ---- the parts that need no network (tested offline) -------------------------------------------------------------

def _norm(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def clean_text(s, limit):
    """YouTube refuses < and > in titles and descriptions; titles <= 100 chars, descriptions <= 5000 bytes."""
    s = s.replace("<", "‹").replace(">", "›")
    if limit == 100:
        return s[:100]
    b = s.encode("utf-8")
    if len(b) <= limit:
        return s
    return b[:limit].decode("utf-8", "ignore").rsplit("\n", 1)[0]


def handle_of(url):
    """'https://www.youtube.com/@sreday' -> 'sreday' (None when the link names no handle)."""
    m = re.search(r"youtube\.com/@([\w.-]+)", str(url or ""))
    return m.group(1) if m else None


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:                                    # noqa: BLE001 - no tzdata: the offset fallback
        return None


def release_at(ev, cfg):
    """(UTC datetime, note) when the talk videos go public: the event's first day + release_days (35: "exactly 5
    weeks after the conference") at release_time (09:00) in the city's own time zone - youtube.timezones (an IANA
    name per city, so a change to/from summer time between the event and the release is right), else the UTC offset
    of the event's start_time (note says so). A metadata.yml `video_release` (ISO time) overrides it all."""
    yt = cfg.get("youtube") or {}
    if ev.get("video_release"):
        t = datetime.datetime.fromisoformat(str(ev["video_release"]).replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)
        return t.astimezone(datetime.timezone.utc), "video_release in metadata.yml"
    start = datetime.datetime.fromisoformat(ev["start_time"])
    day = start.date() + datetime.timedelta(days=int(yt.get("release_days") or 35))
    hh, mm = [int(x) for x in str(yt.get("release_time") or "09:00").split(":")]
    zones = {str(k).lower(): v for k, v in (yt.get("timezones") or {}).items()}
    zname = zones.get(str(ev.get("city") or "").lower())
    tz = _zone(zname) if zname else None
    note = ""
    if tz is None:
        tz = start.tzinfo or datetime.timezone.utc
        note = ("no time zone for %s in settings youtube.timezones - %02d:%02d at the event's UTC offset (%s); summer "
                "time may move it an hour" % (ev.get("city"), hh, mm, start.strftime("%z") or "UTC"))
    local = datetime.datetime.combine(day, datetime.time(hh, mm), tzinfo=tz)
    return local.astimezone(datetime.timezone.utc), note


def release_plan(ev, cfg, now=None):
    """(privacy, publish_at or None, note): private + YouTube publishes it at the release; public when that's past."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    when, note = release_at(ev, cfg)
    if when > now + datetime.timedelta(minutes=15):
        return "private", when, note
    return "public", None, note


def tags_for(ev, talk):
    tags = [ev["brand_name"], ev["title"], "%s %s" % (ev["brand_name"], ev["city"]), ev["city"]]
    tags += {"sreday": ["SRE", "site reliability engineering"], "llmday": ["LLM", "AI engineering"],
             "platformday": ["platform engineering"], "pec": ["prompt engineering", "LLM"],
             "devopsnotdead": ["DevOps"]}.get(ev["brand"], [])
    out = []
    for t in tags:
        if t and t not in out:
            out.append(t)
    while len(",".join(out)) > 450:
        out.pop()
    return out


def video_body(ev, cfg, talk, chapters, written=None, now=None):
    """The videos.insert body and its notes: everything that used to be pasted from YouTube items.txt."""
    title, lines = youtube.block(ev, cfg, talk, chapters, written)
    desc = "\n".join(lines).rstrip()
    yt = cfg.get("youtube") or {}
    privacy, when, note = release_plan(ev, cfg, now)
    body = {"snippet": {"title": clean_text(title, 100), "description": clean_text(desc, 4900),
                        "tags": tags_for(ev, talk), "categoryId": str(yt.get("category_id") or 28),
                        "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "embeddable": True,
                       "license": "youtube"}}
    if when:
        body["status"]["publishAt"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return body, note


def thumbnail_for(ev, talk, get=None):
    """((bytes, mime), "") of the talk's thumbnail from the website's teasers folder, or (None, why)."""
    url = youtube.thumbnail_url(ev, talk)
    if not url:
        return None, "the brand has no domain - no thumbnail"
    try:
        r = (get or requests.get)(url, timeout=60)
    except requests.RequestException as e:
        return None, "thumbnail unreachable (%s)" % e
    if r.status_code != 200 or not r.content:
        return None, "no thumbnail at %s yet (%d)" % (url, r.status_code)
    data = r.content
    if len(data) <= 2 * 1024 * 1024:
        return (data, "image/png"), ""
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90, optimize=True)
    return (out.getvalue(), "image/jpeg"), ""


def readd_plan(current, wanted):
    """current: video ids in playlist order; wanted: our talks' video ids in page order. The talks already in order
    at the front stay; from the first one out of place, the rest are taken out and added again in page order.
    Returns (keep, readd)."""
    ours = [v for v in current if v in set(wanted)]
    k = 0
    while k < len(ours) and k < len(wanted) and ours[k] == wanted[k]:
        k += 1
    return wanted[:k], wanted[k:]


# ---- one event -------------------------------------------------------------------------------------------------

def state_path(run):
    return os.path.join(run.dir, "youtube.json")


def load(run):
    p = state_path(run)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(run, d):
    p = state_path(run)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p)


def redone_since(t, rec):
    """The talk was redone after its upload: the new version goes up as a new video."""
    return bool(rec.get("id")) and len(t.get("redone") or []) > int(rec.get("redone") or 0)


def channel_for(cfg, brand):
    return str(((cfg.get("youtube") or {}).get("channels") or {}).get(brand) or "")


def event(run, keys=None, yt=None, now=None):
    """Upload the event's finished, checked talks that are not on YouTube yet (or were redone since), then finish
    what an earlier pass could not (thumbnail, captions, playlist) and keep the playlist in the page's order.
    Yields log lines. No sign-in for the brand: one line, nothing else."""
    ev, cfg = run.ev, run.cfg
    try:
        yt = yt or YouTube.for_brand(ev["brand"])
    except LookupError as e:
        yield "youtube: %s" % e
        return
    done = load(run)
    talks = {t["key"]: t for t in events.talks(ev)}
    todo = [k for k, t in sorted(run.state["talks"].items())
            if t.get("finished") and t.get("selfcheck_ok") and (not keys or k in keys)
            and (not (done.get(k) or {}).get("id") or redone_since(t, done[k]))]
    playlist_title = str((cfg.get("youtube") or {}).get("playlist_title") or "{event}").format(event=ev["title"])
    want_id = channel_for(cfg, ev["brand"]) or yt.c.get("channel_id")
    yt.check_channel(want_id, handle_of(ev.get("channel_url")))
    playlist_id = None
    for key in todo:
        t, talk = run.state["talks"][key], talks.get(key)
        final = run.final_path(key)
        if not talk:
            yield "youtube: %s is not in the lineup any more - not uploaded" % key
            continue
        if not os.path.exists(final):
            yield "youtube: %s - the final video is missing (%s) - not uploaded" % (key, final)
            continue
        ch = ((t.get("steps") or {}).get("chapters") or {}).get("chapters") or []
        body, note = video_body(ev, cfg, talk, ch, t.get("yt_title"), now)
        rec = {"title": body["snippet"]["title"], "privacy": body["status"]["privacyStatus"],
               "redone": len(t.get("redone") or []), "file": os.path.basename(final)}
        if body["status"].get("publishAt"):
            rec["publish_at"] = body["status"]["publishAt"]
        old = done.get(key) or {}
        if old.get("id"):                                 # a redone talk: the new video replaces the old one
            gone = False
            st = yt.status(old["id"]) or {}
            never_public = (st.get("status") or {}).get("privacyStatus") == "private" and old.get("publish_at") and \
                old["publish_at"] > (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
            if never_public:
                gone = yt.delete(old["id"])[0]
            rec["replaces"] = (old.get("replaces") or []) + [old["id"]]
            rec["old_deleted"] = bool(gone)
            if old.get("sheet"):
                rec["sheet"] = old["sheet"]
        try:
            vid = yt.upload(final, body, run.note)
        except QuotaExceeded:
            yield "youtube: the day's upload limit is reached - the rest goes up on the next pass (tomorrow)"
            break
        rec.update(id=vid, url="https://youtu.be/%s" % vid, uploaded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   studio="https://studio.youtube.com/video/%s/edit" % vid)
        if note:
            rec["note"] = note
        done[key] = rec
        save(run, done)                                   # the id first: a crash later never uploads it twice
        thumb, why = thumbnail_for(ev, talk)
        rec["thumbnail"] = yt.thumbnail(vid, *thumb)[0] if thumb else False
        if why:
            rec["thumbnail_note"] = why
        srt = run.srt_path(key)
        rec["captions"] = None
        if os.path.exists(srt):
            with open(srt, encoding="utf-8-sig") as f:
                ok, msg = yt.captions(vid, f.read())
            rec["captions"] = ok
            if not ok:
                rec["captions_note"] = msg
        try:
            if playlist_id is None:
                playlist_id, made = yt.playlist(playlist_title)
                if made:
                    yield "youtube: made the playlist %s (public; the videos show as they go public)" % playlist_title
            rec["playlist"], msg = yt.add_to_playlist(playlist_id, vid)
            if not rec["playlist"]:
                rec["playlist_note"] = msg
        except Exception as e:                           # noqa: BLE001
            rec["playlist"], rec["playlist_note"] = False, str(e)[:200]
        st = yt.status(vid) or {}
        s = st.get("status") or {}
        rec["upload_status"] = s.get("uploadStatus")
        if s.get("rejectionReason") or s.get("failureReason"):
            rec["problem"] = s.get("rejectionReason") or s.get("failureReason")
        done[key] = rec
        save(run, done)
        yield "youtube: %s -> %s (%s%s)%s%s" % (
            key, rec["url"], rec["privacy"], ", public at %s UTC" % rec["publish_at"][:16].replace("T", " ")
            if rec.get("publish_at") else "", "" if rec.get("thumbnail") else " - no thumbnail: " +
            rec.get("thumbnail_note", "refused"), (" - replaces %s (%s)" % (rec["replaces"][-1], "deleted, it was never "
            "public" if rec.get("old_deleted") else "still on YouTube: delete it in Studio")) if rec.get("replaces") else "")
    yield from repair(yt, run, done, talks, playlist_title)
    yield from playlist_order(yt, run, done, playlist_title)


def repair(yt, run, done, talks, playlist_title):
    """What an upload could not finish is tried again every pass: the thumbnail, the captions, the playlist. Stops at
    the first refusal for the day's quota."""
    playlist_id, changed = None, False
    limit = lambda text: "quota" in str(text).lower()
    for key, rec in sorted(done.items()):
        talk = talks.get(key)
        if not rec.get("id") or not talk:
            continue
        got = []
        try:
            if rec.get("thumbnail") is not True:
                thumb, _ = thumbnail_for(run.ev, talk)
                if thumb:
                    ok, msg = yt.thumbnail(rec["id"], *thumb)
                    if not ok and limit(msg):
                        break
                    if ok:
                        rec["thumbnail"] = True
                        rec.pop("thumbnail_note", None)
                        got.append("thumbnail")
            srt = run.srt_path(key)
            if rec.get("captions") is not True and os.path.exists(srt):
                with open(srt, encoding="utf-8-sig") as f:
                    ok, msg = yt.captions(rec["id"], f.read())
                if not ok and limit(msg):
                    break
                if ok:
                    rec["captions"] = True
                    rec.pop("captions_note", None)
                    got.append("captions")
            if rec.get("playlist") is not True:
                try:
                    if playlist_id is None:
                        playlist_id = yt.playlist(playlist_title)[0]
                    ok, msg = yt.add_to_playlist(playlist_id, rec["id"])
                except RuntimeError as e:
                    ok, msg = False, str(e)
                if not ok and limit(msg):
                    break
                if ok:
                    rec["playlist"] = True
                    rec.pop("playlist_note", None)
                    got.append("playlist")
        finally:
            if got:
                changed = True
                yield "youtube: %s - added later: %s" % (key, ", ".join(got))
    if changed:
        save(run, done)


PAUSE = time.sleep                                       # tests swap it out


def playlist_order(yt, run, done, playlist_title):
    """The event's playlist in the order of the event page (talks.csv), by adding the talks in that order. Taking
    out and adding are 50 units each; a playlist already in order costs one list call. Never fails the pass."""
    if (run.cfg.get("youtube") or {}).get("playlist_sort") is False:
        return
    try:
        order = [t["key"] for t in events.talks(run.ev)]
        vid_of = {k: r["id"] for k, r in done.items() if r.get("id") and r.get("playlist")}
        wanted = [vid_of[k] for k in order if k in vid_of]
        if not wanted:
            return
        pid = yt.playlist(playlist_title)[0]
        items = [(i, v) for i, v, _ in sorted(yt.playlist_items(pid), key=lambda x: x[2])]
        keep, readd = readd_plan([v for _, v in items], wanted)
        if not readd:
            return
        key_of = {v: k for k, v in vid_of.items()}
        items_of = {}
        for i, v in items:
            items_of.setdefault(v, []).append(i)
        for v in readd:
            for item in items_of.get(v, []):
                ok, msg = yt.remove_item(item)
                if not ok:
                    yield "youtube: playlist not sorted - could not take out %s (%s)" % (key_of[v], msg[:120])
                    return
        for n, v in enumerate(readd):
            if n:
                PAUSE(1.2)                               # YouTube keeps the time added to the second
            ok, msg = yt.add_to_playlist(pid, v)
            if not ok:
                yield "youtube: playlist - %d of %d talks added back in order, then: %s (the next pass goes on)" % (
                    n, len(readd), msg[:160])
                return
        yield "youtube: playlist in the event page's order (%d kept, %d added in order)" % (len(keep), len(readd))
    except Exception as e:                               # noqa: BLE001 - sorting never fails the pass
        yield "youtube: playlist not sorted (%s)" % str(e)[:200]


def login(brand, client_file, cfg):
    """One-time per channel, on this computer: the person signs in in the browser and picks the brand's channel;
    the token goes to ~/.live-factory/youtube/<brand>.json (never printed). Refuses a channel that isn't the brand's."""
    import http.server
    import secrets
    import threading
    import webbrowser
    with open(client_file, encoding="utf-8") as f:
        c = json.load(f)
    c = c.get("installed") or c.get("web") or c
    state, got = secrets.token_urlsafe(16), {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Done - you can close this tab and go back to the terminal.")

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = "http://127.0.0.1:%d/" % srv.server_port
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state})
    print("Opening the browser. Sign in, and on Google's 'Choose an account or brand account' screen pick the %s "
          "channel itself:\n%s" % (brand, url))
    webbrowser.open(url)
    th = threading.Thread(target=srv.handle_request)
    th.start()
    th.join(600)
    if got.get("state") != state or "code" not in got:
        raise SystemExit("no sign-in came back: %s" % got.get("error", "timeout"))
    r = requests.post(TOKEN_URL, data={"code": got["code"], "client_id": c["client_id"],
                                       "client_secret": c["client_secret"], "redirect_uri": redirect,
                                       "grant_type": "authorization_code"}, timeout=60).json()
    if "refresh_token" not in r:
        raise SystemExit("Google gave no refresh token (%s)" % r.get("error", "?"))
    creds = {"client_id": c["client_id"], "client_secret": c["client_secret"], "refresh_token": r["refresh_token"]}
    ch = YouTube(creds).channel()
    want = channel_for(cfg, brand)
    handle = (ch["snippet"].get("customUrl") or "").lstrip("@")
    expected = (((cfg.get("youtube") or {}).get("handles") or {}).get(brand) or brand)
    if (want and ch["id"] != want) or (not want and handle.lower() != str(expected).lower()):
        raise SystemExit("signed in to %s (%s, @%s) - not the %s channel. Run it again and pick the %s channel on "
                         "Google's account screen." % (ch["snippet"].get("title"), ch["id"], handle, brand, brand))
    creds["channel_id"] = ch["id"]
    p = token_path(brand)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(creds, f)
    print("\nSigned in to the channel %s (%s, @%s). Saved for this computer only (%s, outside every repo)."
          % (ch["snippet"].get("title"), ch["id"], handle, p))
    if not want:
        print("Add it to settings.yml under youtube.channels:  %s: %s" % (brand, ch["id"]))
