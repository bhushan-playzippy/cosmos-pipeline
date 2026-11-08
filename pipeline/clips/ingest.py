"""Media ingestion for rights-cleared sources only.

Allowed pathways:
  file   a file the creator/rights holder gave us, placed under assets/sources/<channel>/ (checksummed)
  url    a direct file URL on a host in ingest.allowed_hosts (licensed/public-domain archives such as NASA)
Never: YouTube, TikTok, Instagram, Facebook, X or any page-scraping downloader. YouTube's Terms only allow
downloads YouTube itself authorizes, and a clip being public is not permission.
"""
import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from . import registry, rights, transcript
from ..util import ROOT, log, event, read_json, write_json

BLOCKED = ("youtube.com", "youtu.be", "googlevideo.com", "tiktok.com", "instagram.com", "facebook.com", "fb.watch",
           "twitter.com", "x.com", "twitch.tv", "kick.com", "rumble.com", "streamable.com", "reddit.com",
           "redd.it", "vimeo.com", "dailymotion.com", "snapchat.com")
UA = {"User-Agent": "cosmos-pipeline-clips/1.0 (authorized-source ingestion)"}


class UnauthorizedSource(RuntimeError):
    pass


def check_url(url, cfg):
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme != "https":
        raise UnauthorizedSource(f"only https downloads are allowed: {url}")
    if any(host == b or host.endswith("." + b) for b in BLOCKED):
        raise UnauthorizedSource(f"{host} is never downloaded from (platform terms; ask the creator for the file)")
    allowed = cfg.get("ingest", {}).get("allowed_hosts", [])
    if not any(host == a or host.endswith("." + a) for a in allowed):
        raise UnauthorizedSource(f"{host} is not in ingest.allowed_hosts")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url, dest, cfg):
    check_url(url, cfg)
    cap = cfg.get("ingest", {}).get("max_mb", 600) * 1024 * 1024
    with requests.get(url, headers=UA, stream=True, timeout=60, allow_redirects=True) as r:
        check_url(r.url, cfg)  # a redirect must land on an allowed host too
        r.raise_for_status()
        size = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > cap:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError(f"{url} is larger than ingest.max_mb")
                f.write(chunk)
        tmp.replace(dest)
    return dest


def _nasa_files(src):
    """Resolves a NASA library item to its mp4 and caption URLs via the official asset manifest."""
    r = requests.get(f"https://images-api.nasa.gov/asset/{quote(src['nasa_id'])}", headers=UA, timeout=30)
    r.raise_for_status()
    hrefs = [i["href"].replace("http://", "https://") for i in r.json()["collection"]["items"]]
    pick = lambda suffixes: next((h for s in suffixes for h in hrefs if h.endswith(s)), None)
    return pick(["~large.mp4", "~medium.mp4", "~orig.mp4", "~mobile.mp4"]), pick([".srt", ".vtt"])


def ingest(cfg, source_id):
    """Gets the media + transcript of one cleared source into data/channels/<id>/media/. Returns the record."""
    if not rights.is_cleared(cfg, source_id):
        raise UnauthorizedSource(f"{source_id} is not rights-cleared: not ingesting")
    src = registry.get(cfg, source_id)
    media = dict(src.get("media") or {})
    folder = cfg["_paths"]["data"] / "media"
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w-]+", "_", source_id)[:80]
    if src.get("platform") == "nasa" and not media.get("location"):
        media["kind"] = "url"
        media["location"], cap_url = _nasa_files(src)
        if cap_url:
            src["transcript"] = {"kind": cap_url.rsplit(".", 1)[-1], "location": cap_url}
    if media.get("kind") == "file":
        p = (ROOT / media["location"]).resolve()
        base = (ROOT / "assets" / "sources" / cfg["channel"]["id"]).resolve()
        if base not in p.parents:
            raise UnauthorizedSource(f"supplied files must live under {base.relative_to(ROOT)}/")
        if not p.exists():
            raise FileNotFoundError(p)
        local = p
    elif media.get("kind") == "url" and media.get("location"):
        local = folder / f"{stem}.mp4"
        if not local.exists():
            log(f"downloading {source_id} from {urlparse(media['location']).hostname}")
            _download(media["location"], local, cfg)
    else:
        raise UnauthorizedSource(f"{source_id} has no authorized media pathway (file or allowlisted url)")
    media["local"] = str(local.relative_to(ROOT)) if ROOT in local.parents else str(local)
    media["sha256"] = media.get("sha256") or _sha256(local)
    if media.get("expected_sha256") and media["expected_sha256"] != media["sha256"]:
        raise UnauthorizedSource(f"{source_id}: file checksum differs from the one recorded with the permission")

    tr = dict(src.get("transcript") or {})
    cues = None
    if tr.get("location"):
        loc = tr["location"]
        if loc.startswith("https://"):
            tpath = folder / f"{stem}.{tr.get('kind', 'srt')}"
            if not tpath.exists():
                _download(loc, tpath, cfg)
        else:
            tpath = ROOT / loc
        cues = transcript.load(tpath)
        tr["local"] = str(tpath.relative_to(ROOT)) if ROOT in tpath.parents else str(tpath)
    if not cues:
        cues = transcript.whisper(local, cfg.get("ingest", {}).get("whisper_model", "small.en"))
        if cues:
            tpath = folder / f"{stem}.whisper.json"
            write_json(tpath, cues)
            tr = {"kind": "whisper", "local": str(tpath.relative_to(ROOT))}
    if not cues:
        registry.update(cfg, source_id, media=media, status="eligible",
                        incidents=src.get("incidents", []) + [{"date": registry.today(), "kind": "no_transcript"}])
        raise RuntimeError(f"{source_id}: no transcript (supply an .srt/.vtt, or pip install faster-whisper)")
    registry.update(cfg, source_id, media=media, transcript=tr, status="ingested")
    event("ingested", source=source_id, sha256=media["sha256"], cues=len(cues))
    log(f"ingested {source_id}: {len(cues)} caption cues")
    return registry.get(cfg, source_id)


def cues_of(src):
    tr = src["transcript"]
    p = ROOT / tr["local"]
    if tr.get("kind") == "whisper":
        return read_json(p, [])
    return transcript.load(p)


def media_path(src):
    return ROOT / src["media"]["local"]


def register_file(cfg, path, rights_rec, title, creator, transcript_path=None, url=None, youtube_id=None):
    """Registers a creator-supplied file (copied into assets/sources/<channel>/) with its rights record.
    youtube_id: the file is that YouTube video (e.g. raw footage from the creator's campaign): it is attached to
    the discovered lead yt:<id> instead of creating a new source."""
    path = Path(path)
    dest_dir = ROOT / "assets" / "sources" / cfg["channel"]["id"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if path.resolve() != dest.resolve():
        shutil.copy2(path, dest)
    sha = _sha256(dest)
    rec = {"id": f"yt:{youtube_id}" if youtube_id else f"file:{sha[:12]}", "platform": "file", "title": title,
           "creator": creator, "url": url or (f"https://www.youtube.com/watch?v={youtube_id}" if youtube_id else None),
           "media": {"kind": "file", "location": str(dest.relative_to(ROOT)), "expected_sha256": sha},
           "rights": rights_rec}
    if transcript_path:
        t = dest_dir / Path(transcript_path).name
        if Path(transcript_path).resolve() != t.resolve():
            shutil.copy2(transcript_path, t)
        rec["transcript"] = {"kind": t.suffix.lstrip("."), "location": str(t.relative_to(ROOT))}
    existing = registry.get(cfg, rec["id"])
    registry.upsert(cfg, rec)
    if existing:  # a discovered lead: the supplied file and its permission replace "no media / no rights"
        registry.update(cfg, rec["id"], media=rec["media"], rights=rights_rec or existing.get("rights"),
                        platform="file", **({"transcript": rec["transcript"]} if rec.get("transcript") else {}))
    rights.review(cfg, rec["id"])
    return rec["id"]
