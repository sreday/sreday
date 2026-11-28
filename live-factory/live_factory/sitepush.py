"""Talk videos and the playlist onto the event's website, straight away (Marek 2026-11-28: "every time a playlist or a
video is added, push straight").

After each upload (and at the end of every YouTube pass, for anything a push missed) the event's own website repo
(sreday / llmday / platformday) gets one commit on origin/main:
- `<event>/_db/talks.csv`: the talk's YouTube cell (https://youtu.be/<id>). Only an empty cell, or one holding a video
  this talk's redo replaced - a link someone typed is never overwritten. A blank first header becomes "YouTube" (some
  sheets export it without the name, and the site then ignores the column).
- `<event>/metadata.yml`: `video_release` (the exact publishAt, so the site's countdown and YouTube agree to the second)
  and `youtube_playlist` (only when it has none).
- `home/metadata.yml`: the event card's `youtube_playlist`, only when its entry has the key empty.

The site shows none of it early: before the release a talk page shows "Video ready - premieres in 14d 3h" (PREMIERE
COUNTDOWN in the event generator), the home card's Playlist button waits too, and at the release moment the player
appears by itself.

Built with git's plumbing, like publish.py: a temporary index on origin/main's tree, commit-tree, push. The checkout's
files, branch and index are never touched (other sessions work in it). Never raises; returns what happened.
The WWW sheet keeps its links too (Claude's sheet step): a lineup shipped later from the sheet carries them along.
"""
import os
import re
import shutil
import subprocess
import tempfile

ENABLED = True                                           # tests switch it off


def _git(repo, *args, env=None, data=None):
    exe = shutil.which("git") or (r"C:\Program Files\Git\cmd\git.exe" if os.name == "nt" else "git")
    return subprocess.run([exe, "-C", repo] + list(args), capture_output=True, timeout=180, input=data,
                          env=dict(os.environ, **(env or {})))


def repo_of(ev):
    parent = os.path.dirname(ev["path"])
    return os.path.dirname(parent) if os.path.basename(parent) == "past" else parent


# ---- CSV, edited in place: only the cells we change move, every other byte stays as the sheet exported it ----------

def records(s):
    """[[(start, end), ...], ...]: the span of every field of every record of CSV text s (quotes included)."""
    out, fields, i, n = [], [], 0, len(s)
    if n == 0:
        return out
    while True:
        if i < n and s[i] == '"':
            j = i + 1
            while True:
                k = s.find('"', j)
                if k < 0:
                    j = n
                    break
                if s[k + 1:k + 2] == '"':
                    j = k + 2
                    continue
                j = k + 1
                break
            end = j
        else:
            end = i
        while end < n and s[end] not in ",\r\n":
            end += 1
        fields.append((i, end))
        if end < n and s[end] == ",":
            i = end + 1
            continue
        out.append(fields)
        fields = []
        if end >= n:
            break
        i = end + (2 if s[end:end + 2] == "\r\n" else 1)
        if i >= n:
            break
    return out


def value(s, span):
    v = s[span[0]:span[1]]
    return v[1:-1].replace('""', '"') if v.startswith('"') and v.endswith('"') and len(v) >= 2 else v


def video_id(link):
    m = re.search(r'(?:youtu\.be/|youtube\.com/watch\?v=|youtube\.com/embed/)([\w-]+)', link or "")
    return m.group(1) if m else ""


def set_links(text, links):
    """links: {talk key: (url, [replaced video ids])}. A key is the speaker names, "X (2)" for a second talk.
    Returns (new text, [what happened, per talk])."""
    bom = text.startswith("\ufeff")
    s = text[1:] if bom else text
    recs = records(s)
    if not recs:
        return text, ["talks.csv is empty"]
    head = [value(s, f) for f in recs[0]]
    edits, said = [], []
    if "YouTube" in head:
        col = head.index("YouTube")
    elif head and head[0].strip() == "":
        col = 0
        edits.append((recs[0][0], "YouTube"))
    else:
        return text, ["talks.csv has no YouTube column - links not written"]
    try:
        name_col = head.index("name")
    except ValueError:
        return text, ["talks.csv has no name column - links not written"]
    seen = {}
    rows = {}
    for r in recs[1:]:
        if len(r) <= max(col, name_col):
            continue
        name = value(s, r[name_col]).strip()
        if not name:
            continue
        seen[name] = seen.get(name, 0) + 1
        rows.setdefault(name if seen[name] == 1 else "%s (%d)" % (name, seen[name]), r)
    for key, (url, replaced) in links.items():
        r = rows.get(key)
        if not r:
            said.append("%s: not in talks.csv - no link written" % key)
            continue
        cur = value(s, r[col]).strip()
        if cur == url:
            continue
        if cur and video_id(cur) not in set(replaced or []):
            said.append("%s: kept the link already there (%s)" % (key, cur))
            continue
        edits.append((r[col], url))
        said.append("%s: %s" % (key, url))
    for (a, b), new in sorted(edits, key=lambda e: -e[0][0]):
        s = s[:a] + new + s[b:]
    return ("\ufeff" if bom else "") + s, said


# ---- metadata.yml, edited as text (comments and order stay) -----------------------------------------------------------

def set_top(text, key, val, only_empty=False):
    """Top-level `key: val` in a YAML text; added at the end when missing. only_empty: an existing value stays."""
    nl = "\r\n" if "\r\n" in text else "\n"
    m = re.search(r"(?m)^%s:[ \t]*([^\r\n#]*?)[ \t]*(?:#[^\r\n]*)?(?=\r?$)" % re.escape(key), text)
    if m:
        old = m.group(1).strip().strip('"\'')
        if old == str(val).strip('"') or (only_empty and old):
            return text
        return text[:m.start()] + "%s: %s" % (key, val) + text[m.end():]
    end = "" if text.endswith(("\n", "\r\n")) or not text else nl
    return text + end + "%s: %s%s" % (key, val, nl)


def set_home_playlist(text, folder, url):
    """home/metadata.yml: the youtube_playlist of the entry with url ./<folder>/, only when it is empty."""
    m = re.search(r"(?m)^([ \t]*)url:[ \t]*\.?/?%s/?[ \t]*(?=\r?$)" % re.escape(folder), text)
    if not m:
        return text, False
    nxt = re.search(r"(?m)^[ \t]*-[ \t]", text[m.end():])
    block_end = m.end() + (nxt.start() if nxt else len(text) - m.end())
    p = re.search(r"(?m)^([ \t]*)youtube_playlist:[ \t]*([^\s#]*)[ \t]*(?=\r?$)", text[m.end():block_end])
    if not p or p.group(2):
        return text, False
    a, b = m.end() + p.start(), m.end() + p.end()
    return text[:a] + "%syoutube_playlist: %s" % (p.group(1), url) + text[b:], True


# ---- the push ------------------------------------------------------------------------------------------------------

def push(ev, links=None, playlist_url="", release_iso=""):
    """One commit on origin/main of the event's website repo with what changed. Returns a line for the log."""
    if not ENABLED:
        return "site: not pushed (switched off)"
    try:
        repo = repo_of(ev)
        folder = ev["folder"]
    except (KeyError, TypeError):
        return "site: not pushed - no website folder for the event"
    try:
        r = None
        for attempt in range(3):
            if _git(repo, "fetch", "-q", "origin", "main").returncode != 0:
                return "site: not pushed - git fetch failed (internet / GitHub sign-in); tried again on the next pass"
            base = _git(repo, "rev-parse", "origin/main").stdout.decode().strip()
            where = None
            for cand in (folder, "past/" + folder):
                if _git(repo, "cat-file", "-e", "%s:%s/metadata.yml" % (base, cand)).returncode == 0:
                    where = cand
                    break
            if not where:
                return "site: not pushed - %s is not on main" % folder

            def show(rel):
                x = _git(repo, "show", "%s:%s" % (base, rel))
                return x.stdout.decode("utf-8") if x.returncode == 0 else None

            files, said = {}, []
            if links:
                rel = where + "/_db/talks.csv"
                old = show(rel)
                if old is not None:
                    new, said = set_links(old, links)
                    if new != old:
                        files[rel] = new
            rel = where + "/metadata.yml"
            meta = show(rel) or ""
            new = meta
            if release_iso:
                new = set_top(new, "video_release", '"%s"' % release_iso)
            if playlist_url:
                new = set_top(new, "youtube_playlist", playlist_url, only_empty=True)
            if new != meta:
                files[rel] = new
            if playlist_url:
                home = show("home/metadata.yml")
                if home is not None:
                    h2, ok = set_home_playlist(home, folder, playlist_url)
                    if ok:
                        files["home/metadata.yml"] = h2
                        said.append("playlist on the home page card")
            if not files:
                return "site: up to date" + ("" if not said else " (%s)" % "; ".join(said))
            idx = os.path.join(tempfile.gettempdir(), "live-factory-site-%d.index" % os.getpid())
            env = {"GIT_INDEX_FILE": idx}
            _git(repo, "read-tree", base, env=env)
            for rel, text in files.items():
                blob = _git(repo, "hash-object", "-w", "--stdin", data=text.encode("utf-8")).stdout.decode().strip()
                _git(repo, "update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, rel), env=env)
            tree = _git(repo, "write-tree", env=env).stdout.decode().strip()
            try:
                os.remove(idx)
            except OSError:
                pass
            what = [x for x in (("%d talk video link%s" % (sum(1 for x in said if "youtu" in x),
                                                            "s" if sum(1 for x in said if "youtu" in x) != 1 else ""))
                                if links else "", "the playlist" if playlist_url else "") if x]
            msg = "%s: %s on the site (LIVE factory)" % (ev.get("title") or folder, " and ".join(what) or "video release")
            commit = _git(repo, "commit-tree", tree, "-p", base, "-m", msg).stdout.decode().strip()
            if not commit:
                return "site: not pushed - git commit-tree failed"
            r = _git(repo, "push", "-q", "origin", "%s:refs/heads/main" % commit)
            if r.returncode == 0:
                return "site: pushed - %s (%s)" % ("; ".join(said) or "release time", repo_name(repo))
        return "site: not pushed - main kept moving or the push was refused: %s" % (
            r.stderr.decode(errors="replace")[-200:] if r is not None else "")
    except Exception as ex:                              # noqa: BLE001 - the site never stops an upload pass
        return "site: not pushed - %s" % ex


def repo_name(repo):
    return os.path.basename(os.path.normpath(repo))
