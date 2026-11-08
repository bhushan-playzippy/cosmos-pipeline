"""YouTube upload via the official YouTube Data API v3 (free quota: ~6 uploads/day)."""
import datetime as dt
import json
import os

from .util import ROOT, log

# upload = post videos; youtube = manage videos/channel/playlists; force-ssl = comments
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
# channels.activate() points these at the active channel's credentials (defaults = the original channel)
TOKEN = ROOT / "youtube_token.json"
TOKEN_ENV = "YOUTUBE_TOKEN_JSON"
CLIENT_SECRET = ROOT / "client_secret.json"
EXPECTED_CHANNEL = None  # the profile's youtube_channel_id; uploads refuse to run without a match


class ChannelMismatch(SystemExit):
    """The OAuth token belongs to a different YouTube channel than the active profile. Never upload."""


def check_channel(channel_id):
    if not EXPECTED_CHANNEL:
        raise ChannelMismatch("this profile has no channel.youtube_channel_id: refusing to upload to an unverified "
                              "channel. Run auth-youtube for it and put the printed id in its profile.")
    if channel_id != EXPECTED_CHANNEL:
        raise ChannelMismatch(f"the YouTube token is for channel {channel_id}, but this profile publishes to "
                              f"{EXPECTED_CHANNEL}. Nothing was uploaded. Re-run auth-youtube with the right account.")


def authorize():
    """One-time: opens a browser, you log in with the channel's Google account (pick the right brand channel).
    The token is saved only if it belongs to the profile's channel (or the profile has no channel id yet)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    if not CLIENT_SECRET.exists():
        raise SystemExit(f"{CLIENT_SECRET.name} not found in the project folder (see README step 4).")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    ch = build("youtube", "v3", credentials=creds, cache_discovery=False).channels().list(
        part="snippet", mine=True).execute()["items"][0]
    log(f"signed in as channel {ch['id']} ({ch['snippet']['title']})")
    if EXPECTED_CHANNEL and ch["id"] != EXPECTED_CHANNEL:
        raise ChannelMismatch(f"that login is channel {ch['id']}, but this profile is {EXPECTED_CHANNEL}: token NOT "
                              "saved. Run again and choose the right channel in Google's account picker.")
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json())
    TOKEN.chmod(0o600)
    if not EXPECTED_CHANNEL:
        if not link_profile(ch["id"]):
            log(f"saved {TOKEN.name}. Now set channel.youtube_channel_id: \"{ch['id']}\" in this channel's profile.")
    else:
        log(f"saved {TOKEN.name}. For GitHub Actions, put its contents in the {TOKEN_ENV} secret.")


def link_profile(channel_id):
    """First login of a new channel: writes its id into channels/<id>.yaml (only if the field is still empty)."""
    import re
    from . import channels
    if not channels.ACTIVE or channels.ACTIVE == channels.default_id():
        return False
    path = channels.PROFILES / f"{channels.ACTIVE}.yaml"
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r'(\n  youtube_channel_id: )""', rf'\1"{channel_id}"', text, count=1)
    if not n:
        return False
    path.write_text(new, encoding="utf-8")
    log(f"linked: channel.youtube_channel_id = {channel_id} written to {path.name}; uploads now verify against it")
    return True


def _creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    raw = os.environ.get(TOKEN_ENV) or (TOKEN.read_text() if TOKEN.exists() else None)
    if not raw:
        raise SystemExit(f"No YouTube token for this channel ({TOKEN} / ${TOKEN_ENV}). Run: python main.py auth-youtube")
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if not creds.valid:
        from google.auth.exceptions import RefreshError
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise SystemExit(f"YouTube login expired or was revoked ({e}). Run:  python main.py auth-youtube")
    return creds


class QuotaExceeded(RuntimeError):
    """YouTube's daily API quota is used up; it resets at midnight Pacific time (12:30 IST in winter)."""


def with_retry(fn, what, tries=5):
    """Retries transient failures (5xx, network) with exponential backoff + jitter. Quota errors are not retried."""
    import random
    import socket
    import time
    from googleapiclient.errors import HttpError
    for attempt in range(tries):
        try:
            return fn()
        except HttpError as e:
            # uploadLimitExceeded comes back as 400 (new channel's daily upload cap) or 403; both mean "tomorrow"
            if "uploadLimitExceeded" in str(e) or (e.resp.status == 403 and "quota" in str(e).lower()):
                raise QuotaExceeded(str(e)[:200]) from e
            if e.resp.status not in (500, 502, 503, 504) or attempt == tries - 1:
                raise
            err = e
        except (ConnectionError, socket.timeout, TimeoutError) as e:
            if attempt == tries - 1:
                raise
            err = e
        wait = 2 ** attempt + random.random()
        log(f"{what}: transient error ({str(err)[:80]}), retrying in {wait:.1f}s")
        time.sleep(wait)


def final_title(script):
    title = script["youtube"]["title"][:100]
    if "#shorts" not in title.lower() and len(title) <= 92:
        title += " #Shorts"
    return title


def find_existing(yt, title):
    """Duplicate-upload guard: is a video with this exact title among the channel's latest 50 uploads?
    The same channels.list call verifies the token's channel identity (no extra quota)."""
    ch = with_retry(lambda: yt.channels().list(part="contentDetails", mine=True).execute(), "channel lookup")
    check_channel(ch["items"][0].get("id"))
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = with_retry(lambda: yt.playlistItems().list(part="snippet", playlistId=uploads, maxResults=50).execute(),
                       "uploads lookup").get("items", [])
    for it in items:
        if it["snippet"]["title"].strip() == title.strip():
            return it["snippet"]["resourceId"]["videoId"]
    return None


def set_disclosure_and_verify(yt, vid, cfg, publish_at=None):
    """videos.insert silently drops containsSyntheticMedia, so set it with an update, then check the video's state."""
    st = with_retry(lambda: yt.videos().list(part="status", id=vid).execute(), "verify")["items"][0]["status"]
    if cfg.get("disclosure", {}).get("ai_label", True):
        body = {k: st[k] for k in ("privacyStatus", "publishAt", "selfDeclaredMadeForKids", "license",
                                   "embeddable", "publicStatsViewable") if k in st}
        body["containsSyntheticMedia"] = True
        st = with_retry(lambda: yt.videos().update(part="status", body={"id": vid, "status": body}).execute(),
                        "AI disclosure")["status"]
    problems = []
    if st.get("uploadStatus") not in ("uploaded", "processed"):
        problems.append(f"uploadStatus={st.get('uploadStatus')}")
    if publish_at and not st.get("publishAt"):
        problems.append("publishAt missing")
    if cfg.get("disclosure", {}).get("ai_label", True) and not st.get("containsSyntheticMedia"):
        problems.append("AI disclosure not set")
    return {"ok": not problems, "problems": problems, "privacy": st.get("privacyStatus"),
            "publish_at": st.get("publishAt")}


def upload(video_path, script, cfg, publish_at=None, on_insert=None):
    """publish_at: optional aware datetime; the video is uploaded private and YouTube makes it public then.
    on_insert: called right before videos.insert, so the caller can persist "upload in flight" (if the process
    dies after this point, the outcome is unknown and must be reconciled with YouTube, not blindly retried).
    Returns (video_id, verification) — an existing id if this exact title is already on the channel."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from . import control

    yt_cfg = cfg["youtube"]
    meta = script["youtube"]
    title = final_title(script)
    if control.DRY_RUN:
        log(f"[dry-run] would upload '{title}' scheduled {publish_at}")
        return None, {"ok": True, "problems": [], "dry_run": True}
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    existing = find_existing(yt, title)
    if existing:
        log(f"already on YouTube as {existing}: not uploading again")
        return existing, set_disclosure_and_verify(yt, existing, cfg, publish_at)
    body = {
        "snippet": {
            "title": title,
            "description": meta["description"][:4900],
            "tags": meta.get("tags", [])[:30],
            "categoryId": yt_cfg.get("category_id", "28"),
            "defaultLanguage": cfg["channel"].get("language", "en"),
        },
        "status": {
            "privacyStatus": yt_cfg.get("privacy", "private"),
            "selfDeclaredMadeForKids": yt_cfg.get("made_for_kids", False),
            # Honest AI disclosure: always on when disclosure.ai_label is set, else only for realistic Veo clips.
            "containsSyntheticMedia": bool(cfg.get("disclosure", {}).get("ai_label", True)
                                           or script.get("ai_visuals_realistic")),
        },
    }
    if publish_at:
        # YouTube only accepts publishAt on private videos
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = publish_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if on_insert:
        on_insert()
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video_path), chunksize=-1, resumable=True))
    resp = None
    while resp is None:  # resumable: a retried next_chunk continues where the upload stopped
        _, resp = with_retry(req.next_chunk, "upload")
    vid = resp["id"]
    control.count("uploads")
    # videos.insert has had its own quota bucket (~100 calls/day) since 2026-06-01; see docs/research
    control.count("upload_units", cfg.get("control", {}).get("units_per_upload", 1))
    when = f", goes public {publish_at:%a %d %b %H:%M} IST" if publish_at else ""
    log(f"YouTube upload done: https://youtube.com/shorts/{vid} ({body['status']['privacyStatus']}{when})")
    verification = set_disclosure_and_verify(yt, vid, cfg, publish_at)
    if not verification["ok"]:
        log(f"upload verification problems for {vid}: {verification['problems']}")
    return vid, verification


def reconcile(script, cfg):
    """For an upload whose outcome is unknown: is it on the channel? Returns (video_id, verification) or (None, None)."""
    from googleapiclient.discovery import build
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    vid = find_existing(yt, final_title(script))
    return (vid, set_disclosure_and_verify(yt, vid, cfg)) if vid else (None, None)
