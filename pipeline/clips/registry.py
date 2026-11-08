"""Source registry (data/channels/<id>/sources.json) and segment records (segments.json).

One record per source video: where it came from, who made it, what we are allowed to do with it (rights), the
rights decision, processing status, the segments taken from it and the Shorts published from it.

Source status:  discovered → (rights) eligible | manual_review | rejected | revoked → ingested → segmented
                → exhausted (no unused segment left)
Segment status: candidate → used (a Short was made) | rejected (failed a gate) | skipped (duplicate)
"""
import datetime as dt
import re

from ..util import read_json, write_json


def _p(cfg, name):
    return cfg["_paths"]["data"] / name


def today():
    return dt.date.today().isoformat()


def sources(cfg):
    return read_json(_p(cfg, "sources.json"), {}, strict=True)


def save_sources(cfg, srcs):
    write_json(_p(cfg, "sources.json"), srcs)


def get(cfg, source_id):
    return sources(cfg).get(source_id)


def upsert(cfg, rec):
    """Adds a new source, or merges discovery fields into an existing one. Rights and review are never overwritten
    by discovery: only rights.review() and the human commands change them."""
    srcs = sources(cfg)
    old = srcs.get(rec["id"])
    if old:
        for k, v in rec.items():
            if k not in ("rights", "review", "status", "segments", "shorts", "incidents") and v is not None:
                old[k] = v
    else:
        srcs[rec["id"]] = {"status": "discovered", "discovered": today(), "segments": [], "shorts": [],
                           "incidents": [], "review": {"status": "pending"}, **rec}
    save_sources(cfg, srcs)
    return srcs[rec["id"]]


def update(cfg, source_id, **fields):
    srcs = sources(cfg)
    srcs[source_id].update(fields)
    save_sources(cfg, srcs)
    return srcs[source_id]


def segments(cfg):
    return read_json(_p(cfg, "segments.json"), [], strict=True)


def save_segments(cfg, segs):
    write_json(_p(cfg, "segments.json"), segs)


def mark_segment(cfg, seg_id, **fields):
    segs = segments(cfg)
    for s in segs:
        if s["id"] == seg_id:
            s.update(fields)
    save_segments(cfg, segs)


# ---------------------------------------------------------------- duplicate detection

def shingles(text, n=4):
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def overlap(a, b):
    """Time overlap of two (start, end) ranges as a share of the shorter one."""
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(1e-6, min(a[1] - a[0], b[1] - b[0]))


def is_duplicate(cfg, source_id, start, end, text, existing=None):
    """A segment duplicates an earlier one if it overlaps a segment already taken from the same source, or if its
    words are too similar to any segment already used/queued (same quote re-uploaded by another source).
    Returns the reason, or None."""
    lim_t = cfg["segments"].get("max_time_overlap", 0.3)
    lim_w = cfg["segments"].get("max_text_similarity", 0.35)
    mine = shingles(text)
    for s in existing if existing is not None else segments(cfg):
        if s.get("status") not in ("used", "candidate_selected"):
            continue
        if s["source_id"] == source_id and overlap((start, end), (s["start"], s["end"])) > lim_t:
            return f"overlaps segment {s['id']} of the same source"
        sim = jaccard(mine, shingles(s.get("text", "")))
        if sim > lim_w:
            return f"{sim:.0%} word overlap with segment {s['id']}"
    return None
