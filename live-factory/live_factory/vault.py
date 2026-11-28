"""The sign-ins follow the person, not the computer (Marek 2026-11-28: "I want the youtube sign in to be automatic when
signed with my claude" - "same for descript, no individual api keys").

One file, "live-factory vault.json", in the person's private Drive folder "Claude (private)" (owner-only), holds:
  {"youtube": {brand: {client_id, client_secret, refresh_token, channel_id}}, "descript": "<token>", "made", "from"}

- `vault pack <file>` on a computer that is signed in: everything it has, into one file. Claude uploads that file to
  the Drive folder by a file reference (its content never passes through the chat), then deletes the local copy.
- On a computer with nothing: Claude finds the vault through the person's Drive connector (part of their Claude
  account), saves it, and `vault install <file>` puts each piece where the factory looks for it
  (~/.live-factory/youtube/<brand>.json, the Descript token as `setup` stores it) and deletes the file. On that fetch
  the tokens pass through Claude's context once per computer - Marek's choice (2026-11-28). Every token can be revoked
  any time: Google account > Security > third-party access, and Descript > Settings > API tokens.
"""
import base64
import json
import os
import socket
import time

from . import machine, ytupload

NAME = "live-factory vault.json"


def pack(path):
    """The sign-ins of this computer into one file at path. Returns what went in (brands, descript yes/no)."""
    yt = {}
    d = os.path.dirname(ytupload.token_path("x"))
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if f.endswith(".json"):
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                yt[f[:-5]] = json.load(fh)
    tok = machine.read_token()
    data = {"youtube": yt, "descript": tok or "", "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "from": socket.gethostname()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return sorted(yt), bool(tok)


def _read(path):
    """The vault file as JSON - or as the base64 of it (what the Drive connector hands over)."""
    with open(path, encoding="utf-8") as f:
        raw = f.read().strip()
    if raw.startswith("{"):
        return json.loads(raw)
    return json.loads(base64.b64decode(raw).decode("utf-8"))


def install(path, keep=False):
    """The vault's sign-ins onto this computer; the file is deleted afterwards (keep=False). A YouTube sign-in this
    computer already has is replaced only when the vault's is for the same channel. Returns a list of what was done."""
    data = _read(path)
    done = []
    for brand, creds in sorted((data.get("youtube") or {}).items()):
        if not {"client_id", "client_secret", "refresh_token", "channel_id"} <= set(creds):
            done.append("youtube %s: incomplete in the vault - skipped" % brand)
            continue
        p = ytupload.token_path(brand)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                mine = json.load(f)
            if mine.get("channel_id") != creds["channel_id"]:
                done.append("youtube %s: this computer is signed in to another channel - left as it is" % brand)
                continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(creds, f)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        done.append("youtube %s: signed in (channel %s)" % (brand, creds["channel_id"]))
    tok = (data.get("descript") or "").strip()
    if tok and machine.read_token() != tok:
        machine.store_token(tok)
        done.append("descript: token installed")
    elif tok:
        done.append("descript: already the same token")
    if not keep:
        os.remove(path)
    return done
