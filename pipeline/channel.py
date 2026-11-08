"""Channel management via the YouTube Data API: branding, videos, comments, playlists.

Not possible through the API (do these in YouTube Studio): channel name, profile picture, handle,
monetization / YPP application.
"""
from .publish_youtube import _creds
from .util import log

_VERIFIED = set()  # token files whose channel matched the profile in this process (1 quota unit, checked once)


def _client():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=_creds(), cache_discovery=False)


def _yt():
    """Every call here (sync, comments, withdraw, delete, analytics snapshot) acts as the token's channel, so the
    token is checked against the profile first, like uploads are. A wrong-account token would otherwise make
    sync_status mark every private scheduled video "deleted" (their slots refill: duplicates) and post comments
    as another channel."""
    from . import publish_youtube
    yt = _client()
    key = (str(publish_youtube.TOKEN), publish_youtube.TOKEN_ENV, publish_youtube.EXPECTED_CHANNEL)
    if key not in _VERIFIED:
        items = yt.channels().list(part="id", mine=True).execute().get("items") or [{}]
        publish_youtube.check_channel(items[0].get("id"))
        _VERIFIED.add(key)
    return yt


def info():
    ch = _yt().channels().list(part="snippet,statistics,brandingSettings,status", mine=True).execute()["items"][0]
    return ch


def update_branding(cfg):
    """Sets channel description, keywords, country and language from config.yaml → channel_profile."""
    from .publish_youtube import check_channel
    prof = cfg["channel_profile"]
    yt = _yt()
    ch = yt.channels().list(part="brandingSettings", mine=True).execute()["items"][0]
    check_channel(ch["id"])  # never rebrand the wrong channel
    b = ch["brandingSettings"]
    b.setdefault("channel", {})
    b["channel"]["description"] = prof["description"].strip()
    b["channel"]["keywords"] = " ".join(f'"{k}"' if " " in k else k for k in prof["keywords"])
    b["channel"]["country"] = prof.get("country", "IN")
    b["channel"]["defaultLanguage"] = cfg["channel"].get("language", "en")
    if prof.get("trailer"):
        b["channel"]["unsubscribedTrailer"] = prof["trailer"]
    yt.channels().update(part="brandingSettings", body={"id": ch["id"], "brandingSettings": b}).execute()
    log("channel description and keywords updated")


def upload_banner(path):
    """Uploads a 2560x1440 banner (channelBanners.insert) and sets it on the channel."""
    from googleapiclient.http import MediaFileUpload
    from .publish_youtube import check_channel
    yt = _yt()
    ch = yt.channels().list(part="brandingSettings", mine=True).execute()["items"][0]
    check_channel(ch["id"])
    url = yt.channelBanners().insert(media_body=MediaFileUpload(str(path), mimetype="image/png")).execute()["url"]
    b = ch["brandingSettings"]
    b.setdefault("image", {})["bannerExternalUrl"] = url
    yt.channels().update(part="brandingSettings", body={"id": ch["id"], "brandingSettings": b}).execute()
    log(f"banner set from {path}")


def delete_video(video_id):
    _yt().videos().delete(id=video_id).execute()
    log(f"deleted video {video_id}")


def unschedule(video_id, ai_label=True):
    """Keeps a scheduled video private and removes its publish time (reversible: it can be rescheduled in Studio).
    The other status fields are kept; the AI disclosure is re-asserted because videos.list doesn't return it and
    an update without it would clear it."""
    import time
    yt = _yt()
    st = yt.videos().list(part="status", id=video_id).execute()["items"][0]["status"]
    body = {k: st[k] for k in ("selfDeclaredMadeForKids", "license", "embeddable", "publicStatsViewable") if k in st}
    body["privacyStatus"], body["publishAt"] = "private", None  # null clears the schedule
    if ai_label:
        body["containsSyntheticMedia"] = True
    yt.videos().update(part="status", body={"id": video_id, "status": body}).execute()
    for _ in range(3):  # the update's own response can still show the old schedule: confirm with a fresh read
        time.sleep(2)
        st = yt.videos().list(part="status", id=video_id).execute()["items"][0]["status"]
        if st.get("privacyStatus") == "private" and not st.get("publishAt"):
            log(f"unscheduled {video_id}: private, no publish time")
            return
    raise RuntimeError(f"{video_id} is still scheduled: {st}")


def reschedule(video_id, publish_at, ai_label=True):
    """Moves a scheduled private upload to a new publish time. Refuses anything YouTube no longer shows as
    private + scheduled (it may already be public: re-privating it would hide a live video), and checks the
    token's channel first like every other write."""
    import datetime as dt
    import time
    from .publish_youtube import check_channel
    yt = _yt()
    check_channel(yt.channels().list(part="id", mine=True).execute()["items"][0]["id"])
    st = yt.videos().list(part="status", id=video_id).execute()["items"][0]["status"]
    if st.get("privacyStatus") != "private" or not st.get("publishAt"):
        raise RuntimeError(f"{video_id} is not a scheduled private upload ({st.get('privacyStatus')}); not moving it")
    when = publish_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {k: st[k] for k in ("selfDeclaredMadeForKids", "license", "embeddable", "publicStatsViewable") if k in st}
    body["privacyStatus"], body["publishAt"] = "private", when
    if ai_label:
        body["containsSyntheticMedia"] = True  # not returned by videos.list; an update without it clears it
    yt.videos().update(part="status", body={"id": video_id, "status": body}).execute()
    for _ in range(3):  # confirm with a fresh read, as in unschedule
        time.sleep(2)
        st = yt.videos().list(part="status", id=video_id).execute()["items"][0]["status"]
        if st.get("publishAt", "").replace(".000Z", "Z") == when:
            log(f"rescheduled {video_id} → {when}")
            return
    raise RuntimeError(f"{video_id} publish time did not change: {st}")


def video_status(ids):
    r = _yt().videos().list(part="snippet,status,statistics", id=",".join(ids)).execute()
    return r.get("items", [])


def comment(video_id, text):
    """Posts a top-level comment as the channel (pinning it has no API: pin it in Studio or the app)."""
    _yt().commentThreads().insert(part="snippet", body={"snippet": {
        "videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()
    log(f"comment posted on {video_id}")


def comments(video_id, limit=100):
    """Top-level comments on one video, newest first (read-only, 1 quota unit per page)."""
    r = _yt().commentThreads().list(part="snippet", videoId=video_id, maxResults=min(limit, 100),
                                    order="time", textFormat="plainText").execute()
    out = []
    for t in r.get("items", []):
        c = t["snippet"]["topLevelComment"]["snippet"]
        out.append({"id": t["id"], "text": c.get("textOriginal") or c.get("textDisplay", ""),
                    "author_channel": (c.get("authorChannelId") or {}).get("value"),
                    "likes": c.get("likeCount", 0), "published": c.get("publishedAt", ""),
                    "replies": t["snippet"].get("totalReplyCount", 0)})
    return out


def ensure_playlist(title, description=""):
    yt = _yt()
    for p in yt.playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", []):
        if p["snippet"]["title"] == title:
            return p["id"]
    p = yt.playlists().insert(part="snippet,status", body={
        "snippet": {"title": title, "description": description},
        "status": {"privacyStatus": "public"}}).execute()
    log(f"created playlist {title}")
    return p["id"]


def add_to_playlist(playlist_id, video_id):
    _yt().playlistItems().insert(part="snippet", body={"snippet": {
        "playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
