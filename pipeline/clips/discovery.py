"""Discovery agent: finds candidate source videos and ranks them. Discovery never grants permission: every
candidate enters the registry as "discovered" and goes through rights.review() before anything is downloaded.

Discovery paths:
  nasa     NASA Image and Video Library API (official, no key). NASA media is generally public domain; the rights
           agent still sends it to a human for a third-party-content check unless auto_approve_public_domain is on.
  creators watchlist in discovery.creators: each creator's recent uploads (metadata only, ~2 quota units per
           creator). A creator's `rights` (e.g. a clip campaign you joined) is copied onto their new videos as a
           starting record; the rights agent still decides, and a human still confirms.
  youtube  YouTube Data API search (metadata only, 100 quota units per search, capped per day). Results are creator
           leads for permission outreach. Media is never fetched from YouTube: the file comes from the
           creator/campaign (inbox).
  inbox    assets/sources/<channel>/inbox/*.mp4 with a <name>.rights.yaml sidecar (creator-supplied files).
"""
import datetime as dt
import math
from pathlib import Path

import requests

from . import registry, rights
from ..util import ROOT, log, event

UA = {"User-Agent": "cosmos-pipeline-clips/1.0"}
NASA_GUIDELINES = "https://www.nasa.gov/nasa-brand-center/images-and-media/"


def nasa(cfg, query, limit=20):
    r = requests.get("https://images-api.nasa.gov/search", params={"q": query, "media_type": "video",
                                                                   "page_size": limit}, headers=UA, timeout=30)
    r.raise_for_status()
    found = []
    for it in r.json()["collection"]["items"][:limit]:
        m = it["data"][0]
        nid = m["nasa_id"]
        rec = {"id": f"nasa:{nid}", "platform": "nasa", "nasa_id": nid, "title": m.get("title", nid),
               "creator": f"NASA {m.get('center', '')}".strip(), "published": (m.get("date_created") or "")[:10],
               "description": (m.get("description") or "")[:1500], "keywords": m.get("keywords", [])[:20],
               "url": f"https://images.nasa.gov/details/{nid}", "discovery_query": query, "discovered_via": "nasa",
               "rights": {"pathway": "public_domain", "holder": "NASA", "licence": "public-domain",
                          "evidence": NASA_GUIDELINES, "commercial_use": True, "attribution_required": True,
                          "attribution_text": "Video: NASA",
                          "permitted_uses": ["edit", "publish_youtube", "monetize"],
                          "restrictions": ["must not imply NASA endorsement", "no NASA logo/insignia as branding",
                                           "identifiable people: no commercial endorsement use",
                                           "check for third-party music or footage credited in the video"]}}
        registry.upsert(cfg, rec)
        found.append(rec["id"])
    return found


def _yt():
    """Public metadata reads: an API key (discovery.api_key_env) if set, else this channel's own OAuth token."""
    import os
    from googleapiclient.discovery import build
    from ..publish_youtube import _creds
    key = os.environ.get(_yt.key_env or "")
    if key:
        return build("youtube", "v3", developerKey=key, cache_discovery=False)
    return build("youtube", "v3", credentials=_creds(), cache_discovery=False)


_yt.key_env = None


def _video_rec(v, query, via):
    sn, st = v["snippet"], v.get("statistics", {})
    return {"id": f"yt:{v['id']}", "platform": "youtube", "youtube_id": v["id"], "title": sn["title"],
            "creator": sn["channelTitle"], "creator_channel_id": sn["channelId"], "published": sn["publishedAt"][:10],
            "description": sn.get("description", "")[:1500], "keywords": sn.get("tags", [])[:20],
            "url": f"https://www.youtube.com/watch?v={v['id']}", "views": int(st.get("viewCount", 0) or 0),
            "duration": v.get("contentDetails", {}).get("duration"),
            "youtube_license": v.get("status", {}).get("license"), "discovery_query": query, "discovered_via": via}


def creators(cfg):
    """Recent uploads of each watchlisted creator (discovery.creators: handle/channel_id, optional rights)."""
    from ..publish_youtube import with_retry
    yt = _yt()
    found = []
    for c in cfg["discovery"].get("creators") or []:
        try:
            q = {"forHandle": c["handle"]} if c.get("handle") else {"id": c["channel_id"]}
            ch = with_retry(lambda: yt.channels().list(part="contentDetails,snippet", **q).execute(), "creator")
            if not ch.get("items"):
                log(f"creator {c.get('handle') or c.get('channel_id')}: not found")
                continue
            uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            items = with_retry(lambda: yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                                               maxResults=c.get("recent", 10)).execute(), "uploads")
            ids = [i["contentDetails"]["videoId"] for i in items.get("items", [])]
            vids = with_retry(lambda: yt.videos().list(part="snippet,statistics,status,contentDetails",
                                                       id=",".join(ids)).execute(), "videos") if ids else {}
            for v in vids.get("items", []):
                rec = _video_rec(v, c.get("handle") or c.get("channel_id"), "creator_watchlist")
                rec["creator_note"] = c.get("note")
                if not registry.get(cfg, rec["id"]) and c.get("rights"):
                    rec["rights"] = c["rights"]  # starting record from the creator's campaign/permission
                registry.upsert(cfg, rec)
                found.append(rec["id"])
        except SystemExit as e:
            log(f"creator discovery skipped: {e}")
            break
        except Exception as e:
            log(f"creator {c.get('handle')}: {type(e).__name__}: {e}")
    return found


def youtube(cfg, query, limit=10):
    """Creator leads from YouTube search (metadata only, 100 quota units)."""
    from ..publish_youtube import with_retry
    yt = _yt()
    res = with_retry(lambda: yt.search().list(part="snippet", q=query, type="video", maxResults=limit,
                                              videoDuration="medium", relevanceLanguage="en").execute(), "search")
    ids = [i["id"]["videoId"] for i in res.get("items", [])]
    if not ids:
        return []
    stats = with_retry(lambda: yt.videos().list(part="statistics,status,contentDetails", id=",".join(ids)).execute(),
                       "video stats")
    found = []
    for v in stats.get("items", []):
        v.setdefault("snippet", next(i["snippet"] for i in res["items"] if i["id"]["videoId"] == v["id"]))
        rec = _video_rec(v, query, "youtube_search")
        registry.upsert(cfg, rec)  # no rights record: a public upload is not permission
        found.append(rec["id"])
    return found


def inbox(cfg):
    """Registers creator-supplied files that arrived with a rights sidecar (see docs/MULTI_CHANNEL.md)."""
    import yaml
    from .ingest import register_file
    box = ROOT / "assets" / "sources" / cfg["channel"]["id"] / "inbox"
    found = []
    for video in sorted(p for p in box.glob("*") if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")):
        side = video.with_suffix(".rights.yaml")
        if not side.exists():
            log(f"inbox: {video.name} has no {side.name}; not registered (no rights record, no use)")
            continue
        meta = yaml.safe_load(side.read_text(encoding="utf-8")) or {}
        tr = next((video.with_suffix(s) for s in (".srt", ".vtt") if video.with_suffix(s).exists()), None)
        sid = register_file(cfg, video, meta.get("rights") or {}, meta.get("title", video.stem),
                            meta.get("creator", "unknown"), tr, meta.get("url"), meta.get("youtube_id"))
        done = box / "registered"
        done.mkdir(exist_ok=True)
        for f in (video, side, tr):
            if f:
                f.rename(done / f.name)
        found.append(sid)
    return found


def score(src, cfg):
    """0–1 ranking. Rights eligibility multiplies (never adds): an uncleared source can't outrank by popularity."""
    kw = {k.lower() for k in cfg["discovery"].get("keywords", [])}
    text = f"{src.get('title', '')} {src.get('description', '')} {' '.join(src.get('keywords', []))}".lower()
    relevance = min(1.0, sum(1 for k in kw if k in text) / 3)
    try:
        age = (dt.date.today() - dt.date.fromisoformat(src.get("published") or "2000-01-01")).days
    except ValueError:
        age = 9999
    freshness = 1.0 if age < 90 else 0.7 if age < 730 else 0.5
    popularity = min(1.0, math.log10(1 + src.get("views", 0)) / 7) if src.get("views") else 0.5
    narrative = 1.0 if src.get("transcript") or src.get("platform") == "nasa" else 0.6
    eligibility = {"approved": 1.0, "manual_review": 0.5}.get((src.get("review") or {}).get("status"), 0.0)
    w = cfg["discovery"].get("weights", {"relevance": 0.4, "freshness": 0.15, "popularity": 0.15, "narrative": 0.3})
    base = (w["relevance"] * relevance + w["freshness"] * freshness + w["popularity"] * popularity
            + w["narrative"] * narrative)
    return round(base * eligibility, 3)


def run(cfg):
    """One discovery pass: inbox, NASA queries, optional YouTube leads; then rights review and ranking."""
    d = cfg["discovery"]
    _yt.key_env = d.get("api_key_env")
    ids = inbox(cfg)
    if d.get("creators"):
        try:
            ids += creators(cfg)
        except SystemExit as e:  # no API key / channel token yet: the other discovery paths still run
            log(f"creator watchlist skipped: {e}")
    for q in d.get("nasa_queries", []):
        try:
            ids += nasa(cfg, q, d.get("per_query", 15))
        except Exception as e:
            log(f"NASA search '{q}' failed: {e}")
    for q in d.get("youtube_queries", [])[:d.get("youtube_searches_per_run", 0)]:
        try:
            ids += youtube(cfg, q)
        except SystemExit as e:
            log(f"YouTube discovery skipped: {e}")
            break
        except Exception as e:
            log(f"YouTube search '{q}' failed: {e}")
    srcs = registry.sources(cfg)
    for sid, src in srcs.items():
        if src["status"] in ("discovered", "manual_review", "eligible"):
            rights.review(cfg, sid)
    srcs = registry.sources(cfg)
    for sid, src in srcs.items():
        src["score"] = score(src, cfg)
    registry.save_sources(cfg, srcs)
    counts = {}
    for s in srcs.values():
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    event("discovery", new_or_seen=len(ids), by_status=counts)
    log(f"discovery: {len(ids)} candidates seen; registry by status {counts}")
    return ids
