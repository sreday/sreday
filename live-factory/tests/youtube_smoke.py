"""Offline test of the autopilot's YouTube upload and the factory's own check (2026-11-27) - no network, no Descript.

  env/bin/python tests/youtube_smoke.py          (Windows: env\\Scripts\\python.exe tests\\youtube_smoke.py)

A fake YouTube (videos, thumbnails, captions, playlists, the day's quota) stands in for the API; a fake run holds
three finished talks. Checks: the release time (5 weeks after the event, 09:00 in the city's zone, summer time
right; the offset fallback; public when the release is past), the upload (private + publishAt, title, description,
tags), thumbnail (JPEG over 2 MB), captions, the playlist made once and reused and kept in the page's order, nothing
uploaded twice, a problematic talk never uploaded, the wrong channel refused, the quota stop and the next pass going
on, a redo of a never-public video deleting the old one. Then selfcheck on generated finals: a clean one passes, a
silent minute and a black stretch fail.
"""
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from live_factory import selfcheck, sitepush, ytupload  # noqa: E402
sitepush.ENABLED = False                       # never a push to a real site from a test (site_tests: a local origin)

TMP = tempfile.mkdtemp(prefix="live-factory-yt-")
FAILS = []
UTC = datetime.timezone.utc


def check(what, ok, detail=""):
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what, (" - " + str(detail)) if detail else ""))
    if not ok:
        FAILS.append(what)


class Resp:
    def __init__(self, code=200, body=None, headers=None, content=b""):
        self.status_code, self._body, self.headers, self.content = code, body or {}, headers or {}, content
        self.text = json.dumps(self._body) if body is not None else ""

    def json(self):
        return self._body


class FakeYouTube:
    """Just enough of the YouTube Data API (and Google's token endpoint) for ytupload."""
    def __init__(self, channel="UCsreday", handle="@sreday"):
        self.channel, self.handle = channel, handle
        self.videos, self.playlists, self.items, self.thumbs, self.caps, self.deleted = {}, {}, [], {}, {}, []
        self.quota_uploads = 100
        self.n = 0
        self.thumb_png = b"\x89PNG" + b"0" * 1000

    def _id(self, p):
        self.n += 1
        return "%s%04d" % (p, self.n)

    def post(self, url, data=None, timeout=None, **kw):
        if "oauth2" in url:
            return Resp(200, {"access_token": "t"})
        return self.request("POST", url, data=data, **kw)

    def get(self, url, timeout=None, **kw):                # the website's thumbnail
        return Resp(200, content=self.thumb_png)

    def put(self, url, data=None, timeout=None, headers=None):
        vid = url.rsplit("/", 1)[1]
        self.videos[vid]["bytes"] = len(data or b"")
        return Resp(200, {"id": vid})

    def request(self, method, url, headers=None, timeout=None, params=None, json=None, data=None):
        params = params or {}
        if url.endswith("/channels"):
            return Resp(200, {"items": [{"id": self.channel, "snippet": {"title": "chan", "customUrl": self.handle},
                                         "contentDetails": {}}]})
        if url.endswith("upload/youtube/v3/videos") and method == "POST":
            if self.quota_uploads <= 0:
                return Resp(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}})
            self.quota_uploads -= 1
            vid = self._id("v")
            self.videos[vid] = {"body": json, "status": dict(json["status"], uploadStatus="uploaded")}
            return Resp(200, {}, headers={"Location": "https://up/" + vid})
        if url.endswith("/thumbnails/set"):
            self.thumbs[params["videoId"]] = (headers or {}).get("Content-Type")
            return Resp(200, {})
        if url.endswith("/captions"):
            vid = re.search(rb'"videoId": "(\w+)"', data).group(1).decode()
            self.caps[vid] = True
            return Resp(200, {})
        if url.endswith("/playlists") and method == "GET":
            return Resp(200, {"items": [{"id": k, "snippet": {"title": v["title"]}} for k, v in self.playlists.items()]})
        if url.endswith("/playlists") and method == "POST":
            pid = self._id("PL")
            self.playlists[pid] = {"title": json["snippet"]["title"], "privacy": json["status"]["privacyStatus"]}
            return Resp(200, {"id": pid})
        if url.endswith("/playlistItems") and method == "POST":
            iid = self._id("i")
            self.items.append({"id": iid, "pl": json["snippet"]["playlistId"], "video": json["snippet"]["resourceId"]["videoId"],
                               "at": "2026-11-27T10:00:%02dZ" % (len(self.items) % 60)})
            return Resp(200, {"id": iid})
        if url.endswith("/playlistItems") and method == "GET":
            return Resp(200, {"items": [{"id": i["id"], "snippet": {"resourceId": {"videoId": i["video"]},
                                                                    "publishedAt": i["at"]}}
                                        for i in self.items if i["pl"] == params["playlistId"]]})
        if url.endswith("/playlistItems") and method == "DELETE":
            self.items = [i for i in self.items if i["id"] != params["id"]]
            return Resp(204)
        if url.endswith("/videos") and method == "GET":
            v = self.videos.get(params["id"])
            return Resp(200, {"items": [{"id": params["id"], "status": v["status"]}] if v else []})
        if url.endswith("/videos") and method == "DELETE":
            self.deleted.append(params["id"])
            self.videos.pop(params["id"], None)
            return Resp(204)
        return Resp(404, {"error": url})


import re  # noqa: E402  (FakeYouTube.request)

EV = {"id": "sreday-2026-dallas-q4", "brand": "sreday", "brand_name": "SREday", "city": "Dallas", "year": "2026",
      "quarter": "Q4", "title": "SREday Dallas 2026 Q4", "start_time": "2026-10-23T09:00:00-05:00",
      "date_string": "October 23, 2026", "domain": "sreday.com", "folder": "2026-dallas-q4",
      "site_url": "https://sreday.com/", "www_url": "https://www.sreday.com", "event_url": "https://sreday.com/2026-dallas-q4/",
      "cfp_url": "", "channel_url": "https://www.youtube.com/@sreday", "video_release": ""}
CFG = {"youtube": {"channels": {"sreday": "UCsreday"}, "tags": {"sreday": ["sre", "observability"]}, "release_days": 35, "release_time": "09:00",
                   "timezones": {"Dallas": "America/Chicago"}, "playlist_title": "{event}", "title_max": 100,
                   "description": ["{event} - {date}", "Grab your ticket for the next {brand}: {www_url}"]}}
TALKS = [{"key": "Ann Lee", "name": "Ann Lee", "title": "Budgets Beat Alerts", "page": "Ann_Lee", "thumb": "Ann Lee.png"},
         {"key": "Bob Ray", "name": "Bob Ray", "title": "On-call Without Heroics", "page": "Bob_Ray", "thumb": "Bob Ray.png"},
         {"key": "Cy Doe", "name": "Cy Doe", "title": "Kafka at the Edge", "page": "Cy_Doe", "thumb": "Cy Doe.png"}]


class FakeRun:
    def __init__(self):
        self.dir, self.ev, self.cfg, self.notes = TMP, EV, CFG, []
        self.state = {"talks": {}}
        for t in TALKS:
            k = t["key"]
            with open(self.final_path(k), "wb") as f:
                f.write(b"\0" * 2048)
            with open(self.srt_path(k), "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:02,000\nHello\n")
            self.state["talks"][k] = {"finished": "x", "selfcheck_ok": k != "Cy Doe", "steps": {"chapters": {"chapters": [
                {"at": "00:00", "title": "Intro"}, {"at": "02:00", "title": "Budgets"}, {"at": "09:00", "title": "Wrap"}]}}}

    def final_path(self, k):
        return os.path.join(TMP, "%s - %s.mp4" % (k, EV["title"]))

    def srt_path(self, k):
        return os.path.join(TMP, "%s.srt" % k)

    def note(self, m):
        self.notes.append(m)


def youtube_tests():
    print("YouTube upload")
    when, note = ytupload.release_at(EV, CFG)
    check("release: Oct 23 + 35 days, 09:00 America/Chicago (CST by then) = 15:00 UTC",
          when == datetime.datetime(2026, 11, 27, 15, 0, tzinfo=UTC) and not note, (when, note))
    ev2 = dict(EV, city="Nowhere")
    when2, note2 = ytupload.release_at(ev2, CFG)
    check("release: no zone for the city -> the event's UTC offset, with a note",
          when2 == datetime.datetime(2026, 11, 27, 14, 0, tzinfo=UTC) and note2, (when2, note2))
    when3, _ = ytupload.release_at(dict(EV, video_release="2026-12-01T17:00:00Z"), CFG)
    check("release: video_release in metadata.yml wins", when3 == datetime.datetime(2026, 12, 1, 17, tzinfo=UTC), when3)
    before = datetime.datetime(2026, 11, 1, tzinfo=UTC)
    after = datetime.datetime(2026, 12, 1, tzinfo=UTC)
    check("plan: before the release -> private + publishAt", ytupload.release_plan(EV, CFG, before)[:2] == ("private", when))
    check("plan: after the release -> public now", ytupload.release_plan(EV, CFG, after)[:2] == ("public", None))

    fake = FakeYouTube()
    ytupload.requests = types.SimpleNamespace(request=fake.request, post=fake.post, put=fake.put, get=fake.get,
                                              RequestException=Exception)
    ytupload.events = types.SimpleNamespace(talks=lambda ev: TALKS)
    ytupload.time.sleep = lambda s: None
    ytupload.PAUSE = lambda s: None
    run = FakeRun()
    yt = ytupload.YouTube({"client_id": "c", "client_secret": "s", "refresh_token": "r", "channel_id": "UCsreday"})
    lines = list(ytupload.event(run, yt=yt, now=before))
    done = ytupload.load(run)
    talks_up = sorted(k for k in done if not k.startswith("_"))
    check("uploads the checked talks only (a problematic one never)", talks_up == ["Ann Lee", "Bob Ray"], talks_up)
    check("a new playlist is noted for hooking to the event's website", (done.get("_playlist") or {}).get("url", "").endswith("PL0002"),
          done.get("_playlist"))
    v = fake.videos.get(done.get("Ann Lee", {}).get("id"), {})
    body = v.get("body") or {}
    check("private, publishAt = the release", body.get("status", {}).get("privacyStatus") == "private" and
          body["status"].get("publishAt") == "2026-11-27T15:00:00Z", body.get("status"))
    check("title: short title | speaker | event", body.get("snippet", {}).get("title") ==
          "Budgets Beat Alerts | Ann Lee | SREday Dallas 2026 Q4", body.get("snippet", {}).get("title"))
    desc = body.get("snippet", {}).get("description", "")
    check("description: event line, ticket link, chapters", desc.startswith("SREday Dallas 2026 Q4 - October 23, 2026") and
          "www.sreday.com" in desc and "02:00 Budgets" in desc, desc[:200])
    check("tags: the channel's own set, then the event and city", body["snippet"]["tags"] ==
          ["sre", "observability", "SREday Dallas 2026 Q4", "SREday Dallas"], body["snippet"].get("tags"))
    check("not for kids, Science & Technology", body["status"]["selfDeclaredMadeForKids"] is False and
          body["snippet"]["categoryId"] == "28")
    check("thumbnail + captions set on both", len(fake.thumbs) == 2 and len(fake.caps) == 2, (fake.thumbs, fake.caps))
    check("one playlist, made public, named after the event", list(p["title"] for p in fake.playlists.values()) ==
          ["SREday Dallas 2026 Q4"] and all(p["privacy"] == "public" for p in fake.playlists.values()), fake.playlists)
    order = [i["video"] for i in sorted(fake.items, key=lambda i: i["at"])]
    check("playlist in the page's order", order == [done["Ann Lee"]["id"], done["Bob Ray"]["id"]], order)
    n = len(fake.videos)
    list(ytupload.event(run, yt=yt, now=before))
    check("a second pass uploads nothing again", len(fake.videos) == n and len(fake.playlists) == 1)

    # the day's quota: Cy's check passes now, the upload is refused -> the next pass takes it
    run.state["talks"]["Cy Doe"]["selfcheck_ok"] = True
    fake.quota_uploads = 0
    lines = list(ytupload.event(run, yt=yt, now=before))
    check("quota: stops with a line, nothing recorded", "Cy Doe" not in ytupload.load(run) and
          any("limit" in l for l in lines), lines)
    fake.quota_uploads = 5
    list(ytupload.event(run, yt=yt, now=before))
    check("quota: the next pass goes on", ytupload.load(run).get("Cy Doe", {}).get("id"))

    # a redo of a video that was never public: the old one is deleted, the new one uploaded
    old = ytupload.load(run)["Bob Ray"]["id"]
    run.state["talks"]["Bob Ray"]["redone"] = [{"at": "x"}]
    lines = list(ytupload.event(run, yt=yt, now=before))
    new = ytupload.load(run)["Bob Ray"]
    check("redo: a new video, the never-public old one deleted", new["id"] != old and old in fake.deleted and
          new.get("old_deleted"), (new, fake.deleted))

    # a retry after a 503 keeps the call's headers (captions need their multipart Content-Type)
    seen, flaky = [], {"n": 0}
    def req503(method, url, headers=None, **kw):
        seen.append(dict(headers or {}))
        flaky["n"] += 1
        return Resp(503, {}) if flaky["n"] == 1 else fake.request(method, url, headers=headers, **kw)
    ytupload.requests = types.SimpleNamespace(request=req503, post=fake.post, put=fake.put, get=fake.get,
                                              RequestException=Exception)
    ok, _ = yt.captions(ytupload.load(run)["Ann Lee"]["id"], "1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    check("a retry keeps the headers", ok and len(seen) == 2 and seen[1].get("Content-Type", "").startswith("multipart"),
          seen)
    ytupload.requests = types.SimpleNamespace(request=fake.request, post=fake.post, put=fake.put, get=fake.get,
                                              RequestException=Exception)

    # one talk's refused upload never stops the others; it is tried again on the next pass
    run2 = FakeRun()
    for k in run2.state["talks"]:
        run2.state["talks"][k]["selfcheck_ok"] = True
    os.remove(ytupload.state_path(run2))
    real = fake.request
    def refuse_ann(method, url, headers=None, json=None, **kw):
        if url.endswith("upload/youtube/v3/videos") and "Ann Lee" in (json or {}).get("snippet", {}).get("title", ""):
            return Resp(400, {"error": "invalidTitle"})
        return real(method, url, headers=headers, json=json, **kw)
    ytupload.requests = types.SimpleNamespace(request=refuse_ann, post=fake.post, put=fake.put, get=fake.get,
                                              RequestException=Exception)
    lines = list(ytupload.event(run2, yt=yt, now=before))
    d2 = ytupload.load(run2)
    check("a refused talk is noted, the next talks still go up", d2["Ann Lee"].get("error") and not d2["Ann Lee"].get("id")
          and d2["Bob Ray"].get("id") and d2["Cy Doe"].get("id"), {k: (v.get("id"), v.get("error")) for k, v in d2.items()
                                                                   if k != ytupload.PLAYLIST})
    ytupload.requests = types.SimpleNamespace(request=fake.request, post=fake.post, put=fake.put, get=fake.get,
                                              RequestException=Exception)
    list(ytupload.event(run2, yt=yt, now=before))
    check("... and goes up on the next pass", ytupload.load(run2)["Ann Lee"].get("id"))

    # one upload pass per event at a time
    open(os.path.join(run2.dir, "youtube.lock"), "w").close()
    lines = list(ytupload.event(run2, yt=yt, now=before))
    check("a second pass at the same time does nothing", any("another upload pass" in l for l in lines), lines)
    os.remove(os.path.join(run2.dir, "youtube.lock"))

    # a thumbnail over 2 MB goes as JPEG
    from PIL import Image
    big = Image.frombytes("RGB", (1280, 720), os.urandom(1280 * 720 * 3))
    buf = io.BytesIO()
    big.save(buf, "PNG")
    fake.thumb_png = buf.getvalue()
    got, why = ytupload.thumbnail_for(EV, TALKS[0], get=fake.get)
    check("thumbnail over 2 MB -> JPEG", got and got[1] == "image/jpeg" and len(fake.thumb_png) > 2 << 20,
          (len(fake.thumb_png), got and got[1], why))

    # the wrong channel is refused before anything goes up
    wrong = FakeYouTube(channel="UCother", handle="@someone")
    ytupload.requests = types.SimpleNamespace(request=wrong.request, post=wrong.post, put=wrong.put, get=wrong.get,
                                              RequestException=Exception)
    try:
        list(ytupload.event(FakeRun(), yt=ytupload.YouTube({"client_id": "c", "client_secret": "s",
                                                            "refresh_token": "r", "channel_id": "UCother"}), now=before))
        check("wrong channel refused", False, wrong.videos)
    except RuntimeError as e:
        check("wrong channel refused", not wrong.videos, str(e)[:120])


def house_tests():
    print("house style")
    from live_factory import youtube
    key = dict(TALKS[0], kind="keynote", title="O11y: The math you miss")
    t = youtube.title_for(EV, CFG, key)
    check("a keynote's title starts with \"Keynote: \"", t == "Keynote: O11y: The math you miss | Ann Lee | SREday Dallas 2026 Q4", t)
    long = dict(TALKS[0], kind="keynote", title="A very long keynote title about reliability, budgets and on-call in large "
                                                "organisations for the people who run them")
    t = youtube.title_for(EV, CFG, long)
    check("... and the whole title stays within 100 characters", len(t) <= 100 and t.startswith("Keynote: "), (len(t), t))
    pl = lambda title, priv="public", n=10, i=None: {"id": i or title, "snippet": {"title": title},
                                                    "status": {"privacyStatus": priv}, "contentDetails": {"itemCount": n}}
    items = [pl("SREday London Q1 2026", i="LON"), pl("SREday Munich 2026 Q2", "unlisted", 2, "MUC2"),
             pl("SREday Munich 2026 Q2", "public", 17, "MUC"), pl("SREday New York City 2025 Q1", i="NY25"),
             pl("SREday NYC 2026 Q2", i="NY26")]
    lon = dict(EV, city="London", year="2026", quarter="Q1")
    check("playlist: a hand-named one is found (\"SREday London Q1 2026\")",
          ytupload.pick_playlist(items, "SREday London 2026 Q1", lon) == "LON")
    check("playlist: of two, the public one with the most videos",
          ytupload.pick_playlist(items, "SREday Munich 2026 Q2", dict(EV, city="Munich", quarter="Q2")) == "MUC")
    check("playlist: NYC = New York City, the right year",
          ytupload.pick_playlist(items, "SREday NYC 2025 Q1", dict(EV, city="NYC", year="2025", quarter="Q1")) == "NY25")
    check("playlist: none fits -> None (made)", ytupload.pick_playlist(items, "SREday Dallas 2026 Q4", EV) is None)
    check("playlist link from the website", ytupload.playlist_id_from(
        "https://www.youtube.com/playlist?list=PL2CAJ_jforK5D2pFxAjp-H1U728dXI8Kb") == "PL2CAJ_jforK5D2pFxAjp-H1U728dXI8Kb")
    sch = {"1": {"items": [{"key": "Bob Ray"}, {"key": ""}, {"key": "Ann Lee"}]}, "2": {"items": [{"key": "Cy Doe"}]}}
    old = ytupload.events
    ytupload.events = types.SimpleNamespace(talks=lambda ev: TALKS, schedule=lambda ev: sch)
    check("playlist order = the website's schedule, track by track", ytupload.site_order(EV) == ["Bob Ray", "Ann Lee", "Cy Doe"],
          ytupload.site_order(EV))
    ytupload.events = old


def ff(*args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + list(args), capture_output=True)
    if r.returncode != 0:
        raise SystemExit("ffmpeg failed: " + r.stderr.decode("utf-8", "replace")[-300:])


def selfcheck_tests():
    print("selfcheck")
    clean = os.path.join(TMP, "clean.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=60", "-f", "lavfi", "-i",
       "anoisesrc=color=pink:amplitude=0.3:duration=60", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
       "-c:a", "aac", clean)
    t = {"probe": {"duration": 62.0}, "steps": {"qa": {"flags": [], "peak": -1.5}, "chapters": {"chapters": []}}}
    probs, notes = selfcheck.check(clean, t, {"true_peak_db": -1})
    check("a clean final passes", not probs, (probs, notes))
    from live_factory.selfcheck import doubts_of
    silent = os.path.join(TMP, "silent.mp4")
    ff("-i", clean, "-af", "volume=enable='between(t,20,32)':volume=0", "-c:v", "copy", "-c:a", "aac", silent)
    probs, _ = selfcheck.check(silent, t, {"true_peak_db": -1})
    check("12 s of silence in a 1-min talk (20%) -> not shipped", any("silence" in p for p in probs), probs)
    long_ = os.path.join(TMP, "long.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=200", "-f", "lavfi", "-i",
       "anoisesrc=color=pink:amplitude=0.3:duration=200", "-shortest", "-af",
       "volume=enable='between(t,100,110)':volume=0", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", long_)
    probs, notes = selfcheck.check(long_, {"probe": {"duration": 205.0}, "steps": t["steps"]}, {"true_peak_db": -1})
    check("one 10 s pause in a longer talk -> shipped, please check", not probs and
          any("silence" in d for d in doubts_of(notes)), (probs, doubts_of(notes)))
    black = os.path.join(TMP, "black.mp4")
    ff("-i", clean, "-vf", "drawbox=enable='between(t,30,45)':x=0:y=0:w=iw:h=ih:color=black:t=fill",
       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", black)
    probs, notes = selfcheck.check(black, t, {"true_peak_db": -1})
    check("15 s of black picture -> shipped, please check", not probs and any("black" in d for d in doubts_of(notes)),
          (probs, doubts_of(notes)))
    probs, _ = selfcheck.check(clean, dict(t, probe={"duration": 120.0}), {"true_peak_db": -1})
    check("half the cut gone -> not shipped", any("of the cut" in p for p in probs), probs)
    fake_verify = lambda f, rec, cfg: (["the cut ends on \"and so\", not on \"thank you\""], [])
    probs, notes = selfcheck.check(clean, t, {"true_peak_db": -1}, {"first": "Hi all", "last": "Thank you."},
                                   verify=fake_verify)
    check("an edge heard differently -> shipped, please check", not probs and
          any(d.startswith("edges:") for d in doubts_of(notes)), (probs, doubts_of(notes)))


def vault_tests():
    print("vault")
    import base64
    from live_factory import machine, vault
    home = os.path.join(TMP, "home")
    real_tp, real_read, real_store = ytupload.token_path, machine.read_token, machine.store_token
    ytupload.token_path = lambda brand: os.path.join(home, "youtube", "%s.json" % brand)
    stored = {}
    machine.read_token = lambda: stored.get("t")
    machine.store_token = lambda t: stored.update(t=t) or "test"
    try:
        creds = {"client_id": "c", "client_secret": "s", "refresh_token": "r", "channel_id": "UCsreday"}
        data = {"youtube": {"sreday": creds, "llmday": {"client_id": "c"}}, "descript": "dtok"}
        f = os.path.join(TMP, "vault.b64")
        with open(f, "w") as fh:                         # as the Drive connector hands it over: base64
            fh.write(base64.b64encode(json.dumps(data).encode()).decode())
        done = vault.install(f)
        check("vault: a channel and the Descript token installed, an incomplete one skipped, the file gone",
              os.path.exists(ytupload.token_path("sreday")) and stored.get("t") == "dtok" and
              not os.path.exists(ytupload.token_path("llmday")) and not os.path.exists(f), done)
        with open(ytupload.token_path("sreday"), "w") as fh:
            json.dump(dict(creds, channel_id="UCother"), fh)
        with open(f, "w") as fh:
            json.dump(data, fh)
        done = vault.install(f)
        check("vault: a computer signed in to another channel is left as it is",
              json.load(open(ytupload.token_path("sreday")))["channel_id"] == "UCother", done)
        out = os.path.join(TMP, "packed.json")
        brands, desc = vault.pack(out)
        check("vault: pack takes this computer's sign-ins", brands == ["sreday"] and desc, (brands, desc))
    finally:
        ytupload.token_path, machine.read_token, machine.store_token = real_tp, real_read, real_store


def site_tests():
    """sitepush: talk links, the playlist and the release go straight onto the event's site repo (a local 'origin')."""
    print("Website push")
    root = os.path.join(TMP, "site")
    origin, work = os.path.join(root, "origin.git"), os.path.join(root, "work")
    os.makedirs(root, exist_ok=True)
    g = lambda cwd, *a: subprocess.run(["git", "-C", cwd] + list(a), capture_output=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], capture_output=True)
    subprocess.run(["git", "clone", "-q", origin, work], capture_output=True)
    ev_dir = os.path.join(work, "2026-testcity-q4")
    os.makedirs(os.path.join(ev_dir, "_db"))
    os.makedirs(os.path.join(work, "home"))
    csv_bytes = (b',status,name,track,title,abstract\r\n'
                 b',confirmed,Ann Lee,1,Budgets Beat Alerts,"Line one\nline, two with ""quotes"""\r\n'
                 b'https://youtu.be/typedByHand,confirmed,Bob Ray,1,On-call,short\r\n'
                 b',confirmed,Cy Doe,2,Kafka,"x"\r\n'
                 b',confirmed,Ann Lee,2,Second talk,y')
    meta = b'start_time: "2026-10-23T09:00:00-05:00"\r\nyoutube_url: https://www.youtube.com/@sreday\r\n'
    home = (b"events:\n  - name: SREday Testcity 2026 Q4\n    url: ./2026-testcity-q4/\n    youtube_playlist:\n"
            b"  - name: Other\n    url: ./2026-other-q4/\n    youtube_playlist:\n")
    for rel, data in (("2026-testcity-q4/_db/talks.csv", csv_bytes), ("2026-testcity-q4/metadata.yml", meta),
                      ("home/metadata.yml", home)):
        with open(os.path.join(work, rel), "wb") as f:
            f.write(data)
    g(work, "-c", "core.autocrlf=false", "add", "-A")
    g(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    g(work, "push", "-q", "origin", "HEAD:main")
    ev = {"path": ev_dir, "folder": "2026-testcity-q4", "title": "SREday Testcity 2026 Q4"}
    show = lambda rel: subprocess.run(["git", "-C", origin, "show", "main:" + rel], capture_output=True).stdout
    sitepush.ENABLED = True
    try:
        line = sitepush.push(ev, {"Ann Lee": ("https://youtu.be/AAA111", []), "Bob Ray": ("https://youtu.be/BBB222", []),
                                  "Ann Lee (2)": ("https://youtu.be/AAA222", [])},
                             "https://www.youtube.com/playlist?list=PLtest1234", "2026-11-27T15:00:00Z")
        c = show("2026-testcity-q4/_db/talks.csv")
        check("site: pushed", line.startswith("site: pushed"), line)
        check("site: blank header -> YouTube, the link in Ann Lee's row, multi-line cell intact",
              c.startswith(b"YouTube,status") and b'https://youtu.be/AAA111,confirmed,Ann Lee,1,Budgets Beat Alerts,"Line one\nline, two with ""quotes"""\r\n' in c, c[:200])
        check("site: a link typed by hand is kept", b"https://youtu.be/typedByHand,confirmed,Bob Ray" in c and "kept" in line, line)
        check("site: a second talk by the same speaker gets its own link", b"https://youtu.be/AAA222,confirmed,Ann Lee,2" in c)
        check("site: every other byte as it was", c.replace(b"YouTube", b"", 1).replace(b"https://youtu.be/AAA111", b"").replace(
            b"https://youtu.be/AAA222", b"") == csv_bytes, c)
        m = show("2026-testcity-q4/metadata.yml")
        check("site: video_release and the playlist in the event's metadata, CRLF kept",
              b'video_release: "2026-11-27T15:00:00Z"\r\n' in m and b"youtube_playlist: https://www.youtube.com/playlist?list=PLtest1234\r\n" in m
              and m.startswith(meta), m)
        h = show("home/metadata.yml")
        check("site: the home card's playlist, only this event's entry",
              b"url: ./2026-testcity-q4/\n    youtube_playlist: https://www.youtube.com/playlist?list=PLtest1234\n" in h
              and h.endswith(b"url: ./2026-other-q4/\n    youtube_playlist:\n"), h)
        again = sitepush.push(ev, {"Ann Lee": ("https://youtu.be/AAA111", [])}, "https://www.youtube.com/playlist?list=PLtest1234",
                              "2026-11-27T15:00:00Z")
        check("site: nothing new -> no commit", again.startswith("site: up to date"), again)
        redo = sitepush.push(ev, {"Ann Lee": ("https://youtu.be/NEW999", ["AAA111"])})
        check("site: a redo replaces its own old link", b"https://youtu.be/NEW999,confirmed,Ann Lee,1" in show(
            "2026-testcity-q4/_db/talks.csv") and redo.startswith("site: pushed"), redo)
        g(work, "pull", "-q", "origin", "main")
        os.makedirs(os.path.join(work, "past"))
        g(work, "mv", "2026-testcity-q4", "past/2026-testcity-q4")
        g(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "archive")
        g(work, "push", "-q", "origin", "HEAD:main")
        moved = sitepush.push(dict(ev, path=os.path.join(work, "past", "2026-testcity-q4")),
                              {"Cy Doe": ("https://youtu.be/CCC333", [])})
        check("site: an event moved to past/ is found there", b"https://youtu.be/CCC333,confirmed,Cy Doe" in show(
            "past/2026-testcity-q4/_db/talks.csv"), moved)
        check("site: the work tree was never touched", not g(work, "status", "--porcelain").stdout.strip(),
              g(work, "status", "--porcelain").stdout)

        # the pass's bookkeeping: pushed once, never again
        R = types.SimpleNamespace(dir=root, ev=dict(ev, path=os.path.join(work, "past", "2026-testcity-q4")))
        done = {"Cy Doe": {"id": "CCC333", "url": "https://youtu.be/CCC333", "publish_at": "2026-11-27T15:00:00Z"},
                ytupload.PLAYLIST: {"url": "https://www.youtube.com/playlist?list=PLtest1234"}}
        lines = list(ytupload.site_sync(R, done, "PLtest1234"))
        check("site_sync: marks what the site has", done["Cy Doe"].get("site") == "CCC333" and done[ytupload.PLAYLIST].get("site"), lines)
        check("site_sync: nothing to push -> silent", list(ytupload.site_sync(R, done, "PLtest1234")) == [])
    finally:
        sitepush.ENABLED = False


if __name__ == "__main__":
    site_tests()
    vault_tests()
    youtube_tests()
    house_tests()
    selfcheck_tests()
    print("\nfiles: " + TMP)
    print("FAILED: " + ", ".join(FAILS) if FAILS else "all good")
    sys.exit(1 if FAILS else 0)
