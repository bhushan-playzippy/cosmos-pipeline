"""Packaging agent: title, description with source credit + licence notice, tags, a few hashtags, pinned comment.
Titles are unique across this channel's queue (the upload duplicate-guard matches by title). A credit line is
attribution, not permission: the permission is the rights record."""
import re

from .. import jobs


def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def used_titles():
    out = set()
    for j in jobs.load():
        if j.get("title") and j["status"] not in ("rejected", "failed", "deleted"):
            out.add(_norm(j["title"]))
    return out


def requirements(src):
    """Rules the campaign/creator attached to their permission (hashtags, tags, description lines, mention)."""
    return (src.get("rights") or {}).get("requirements") or {}


def package(seg, src, narration, cfg):
    p = cfg["package"]
    taken = used_titles()
    options = [t.strip()[:70] for t in narration.get("title_options", []) if t and t.strip()]
    title = next((t for t in options if _norm(t) not in taken and 10 <= len(t) <= 70), None)
    if not title:
        raise ValueError(f"no unique title among {options} (all used or invalid)")
    r = src.get("rights") or {}
    credit = [f"🎬 Source: \"{src.get('title')}\" — {src.get('creator')}"]
    if src.get("url"):
        credit.append(f"   {src['url']}")
    if r.get("licence"):
        credit.append(f"   Used under: {r['licence']}" + (f" ({r.get('holder')})" if r.get("holder") else ""))
    if r.get("attribution_text"):
        credit.append(f"   {r['attribution_text']}")
    credit.append(f"   Clip {seg['start']:.0f}s–{seg['end']:.0f}s of the original. Intro and explanation are ours.")
    if r.get("pathway") == "public_domain" and r.get("holder") == "NASA":
        credit.append("   NASA does not endorse this channel.")
    req = requirements(src)
    if req.get("mention"):
        credit.append(f"   {req['mention']}")
    tags = list(dict.fromkeys([*req.get("tags", []), *p.get("base_tags", []), *src.get("keywords", [])[:8]]))[:15]
    hashtags = list(dict.fromkeys([*req.get("hashtags", []), *p.get("hashtags", ["#Shorts"])]))
    hashtags = hashtags[:max(p.get("max_hashtags", 3), len(req.get("hashtags", [])))]
    desc = "\n".join([narration["intro"], "", narration["outro"], "", *credit, *req.get("description_lines", []), "",
                      cfg.get("disclosure", {}).get("text", "") if cfg.get("disclosure", {}).get("ai_label") else "",
                      "", " ".join(hashtags)]).strip()
    return {"title": title, "title_options": options, "description": desc[:4900], "tags": tags,
            "pinned_comment": (narration.get("pinned_comment") or "")[:140]}
