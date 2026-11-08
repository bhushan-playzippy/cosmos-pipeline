"""Instagram Reels upload via the official Instagram Graph API (resumable upload, no hosting needed).

Needs an Instagram Professional (Business or Creator) account linked to a Facebook Page. See README step 6.
Env (per channel, set by channels.activate): IG_USER_ID / IG_ACCESS_TOKEN for the main channel,
IG_USER_ID_<ID> / IG_ACCESS_TOKEN_<ID> for the others (e.g. IG_USER_ID_CLIPS), so each YouTube channel can post
to its own Instagram account. orchestrator.crosspost decides when (after the Short is public on YouTube).
"""
import os
import time

import requests

from .util import log

USER_ENV, TOKEN_ENV = "IG_USER_ID", "IG_ACCESS_TOKEN"
MAX_HASHTAGS = 5  # Instagram limits a post to 5 hashtags; a few precise tags beat a wall of broad ones anyway


def configured():
    return bool(os.environ.get(USER_ENV) and os.environ.get(TOKEN_ENV))


def caption(script):
    """Caption + at most MAX_HASHTAGS hashtags (the script's own, deduplicated, #AIgenerated kept if present)."""
    ig = script["instagram"]
    tags, seen = [], set()
    for t in ig.get("hashtags", []):
        t = "#" + t.strip().lstrip("#").replace(" ", "")
        if len(t) > 1 and t.lower() not in seen:
            tags.append(t)
            seen.add(t.lower())
    ai = [t for t in tags if t.lower() == "#aigenerated"]
    tags = [t for t in tags if t.lower() != "#aigenerated"][:MAX_HASHTAGS - len(ai)] + ai
    return (ig["caption"].rstrip() + "\n\n" + " ".join(tags))[:2150]


def _base(cfg):
    ig = cfg["instagram"]
    return f"{ig['graph_host'].rstrip('/')}/{ig['api_version']}"


def refresh_token(cfg):
    """Long-lived tokens last ~60 days. Instagram-Login tokens can be refreshed like this."""
    token = os.environ[TOKEN_ENV]
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30)
    r.raise_for_status()
    new = r.json()["access_token"]
    log(f"Instagram token refreshed. Update {TOKEN_ENV} with the new value:")
    print(new)
    return new


def upload(video_path, script, cfg):
    user_id, token = os.environ.get(USER_ENV), os.environ.get(TOKEN_ENV)
    if not (user_id and token):
        raise SystemExit(f"{USER_ENV} / {TOKEN_ENV} missing (see README step 6).")
    base = _base(cfg)

    # 1) create a resumable Reels container
    r = requests.post(f"{base}/{user_id}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption(script),
        "share_to_feed": str(cfg["instagram"].get("share_to_feed", True)).lower(),
        "access_token": token}, timeout=60)
    r.raise_for_status()
    container = r.json()["id"]

    # 2) upload the local file bytes
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        up = requests.post(f"https://rupload.facebook.com/ig-api-upload/{cfg['instagram']['api_version']}/{container}",
                           headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
                           data=f, timeout=600)
    up.raise_for_status()

    # 3) wait for Instagram to process it
    for _ in range(60):
        s = requests.get(f"{base}/{container}", params={"fields": "status_code,status", "access_token": token},
                         timeout=30).json()
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"Instagram processing error: {s}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram processing timed out")

    # 4) publish
    p = requests.post(f"{base}/{user_id}/media_publish",
                      data={"creation_id": container, "access_token": token}, timeout=60)
    p.raise_for_status()
    media_id = p.json()["id"]
    log(f"Instagram Reel published: {media_id}")
    return media_id
