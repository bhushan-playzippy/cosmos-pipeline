"""Rights agent: the mandatory gate between discovery and any use of a source.

Three separate questions, never merged:
  1. May we use this source this way?            ← this module (permission / licence / evidence / expiry)
  2. Does the finished Short follow YouTube policy? ← compliance.py
  3. Is it monetizable?                          ← not decided by code; tracked in reports, never assumed

A source's `rights` record:
  pathway          own | written_permission | clip_campaign | creator_policy | licence | public_domain | platform_remix
                   clip_campaign = an authorized clipping campaign/marketplace (e.g. a creator's Discord/Whop/Vyro
                   campaign) whose terms you accepted; creator_policy = the creator's own published reuse policy
  campaign         clip_campaign: campaign name + where it runs
  requirements     campaign/creator rules every Short must carry: {hashtags, tags, description_lines, mention}
  holder           rights holder (person/organisation)
  licence          e.g. "CC-BY-4.0", "public-domain", "written agreement 2026-11-02"
  evidence         path (under the project) or URL of the proof: signed permission, licence page, metadata
  permitted_uses   e.g. [edit, publish_youtube, monetize]  (written permission must list them)
  commercial_use   true/false
  attribution_required, attribution_text
  restrictions     free text list, shown to the reviewer and in post.txt
  max_clip_seconds optional limit from the agreement
  expires          optional ISO date
  revoked          true once the holder withdraws permission (rights.revoke)

evaluate() returns hard failures (never overridable, not even by a human) and soft failures (need a human
decision). Popularity, a public upload, a remix button, attribution or a short duration are never treated as
permission.
"""
import datetime as dt
import re

from . import registry
from ..util import ROOT, log, event

# Licences that allow editing + commercial reuse without imposing a licence on our Short.
OK_LICENCES = {"cc-by-4.0", "cc-by-3.0", "cc-by-2.0", "cc0", "cc0-1.0", "public-domain"}
LICENCE_PROBLEMS = {
    "nc": "licence forbids commercial use (NC)",
    "nd": "licence forbids derivatives/editing (ND)",
    "sa": "share-alike licence would require licensing the Short the same way; YouTube can't (needs a human)",
}
# Phrases in a source's own description that suggest third-party material inside an otherwise free source.
THIRD_PARTY = re.compile(r"©|\bcopyright\b|courtesy of|used with permission|\bgetty\b|\bshutterstock\b|"
                         r"\bmusic (by|from)\b|\blicensed (from|music)\b|all rights reserved", re.I)


def _evidence_ok(ev):
    if not ev:
        return False
    if re.match(r"https?://", ev):
        return True
    return (ROOT / ev).exists()


def evaluate(src, cfg, on=None):
    """→ {"status": approved|manual_review|rejected|revoked, "hard": [...], "soft": [...]}"""
    rc = cfg.get("rights", {})
    on = on or dt.date.today()
    r = src.get("rights") or {}
    hard, soft = [], []
    if r.get("revoked"):
        return {"status": "revoked", "hard": [f"permission revoked: {r.get('revoked_reason', '')}".strip(": ")],
                "soft": []}
    pathway = r.get("pathway")
    if not pathway:
        return {"status": "manual_review", "hard": [], "soft": [
            "no rights record: a public or popular video is not permission. Get written permission or a licence."]}
    if pathway not in rc.get("allowed_pathways", ["own", "written_permission", "licence", "public_domain"]):
        hard.append(f"pathway '{pathway}' is not allowed for this channel")
    if pathway == "platform_remix":
        hard.append("Shorts remix happens inside the YouTube app; the Data API can't create a remix, and remix does "
                    "not license download/re-upload. Make it by hand in the app instead.")
    lic = (r.get("licence") or "").lower().replace(" ", "-")
    if pathway == "licence":
        parts = set(re.split(r"[-_]", lic))
        bad = [msg for k, msg in LICENCE_PROBLEMS.items() if k in parts]
        if "sa" in parts:
            soft.append(LICENCE_PROBLEMS["sa"])
            bad = [b for b in bad if b != LICENCE_PROBLEMS["sa"]]
        hard += bad
        if not bad and "sa" not in parts and lic not in OK_LICENCES:
            soft.append(f"licence '{r.get('licence')}' is not on the known-good list; a human must read it")
    if pathway in ("clip_campaign", "creator_policy"):
        if pathway == "clip_campaign" and not r.get("campaign"):
            hard.append("clip_campaign needs the campaign's name/where it runs")
        soft.append(f"{pathway.replace('_', ' ')}: a human must confirm the terms cover YouTube Shorts uploads on "
                    "this channel, monetization, length and credit rules")
    if pathway in ("written_permission", "clip_campaign", "creator_policy", "licence", "own", "public_domain") \
            and not _evidence_ok(r.get("evidence")):
        hard.append(f"evidence missing or not found: {r.get('evidence') or '(none)'}")
    if pathway in ("written_permission", "clip_campaign"):
        missing = [u for u in rc.get("required_uses", ["edit", "publish_youtube"]) if u not in r.get("permitted_uses", [])]
        if missing:
            hard.append(f"permission does not cover: {', '.join(missing)}")
    if rc.get("require_commercial", True) and r.get("commercial_use") is False:
        hard.append("commercial use not permitted, and this channel intends to monetize")
    if rc.get("require_commercial", True) and r.get("commercial_use") is None and pathway not in ("own",):
        soft.append("commercial-use status not recorded")
    if r.get("expires"):
        try:
            if dt.date.fromisoformat(str(r["expires"])) < on:
                hard.append(f"permission expired on {r['expires']}")
        except ValueError:
            hard.append(f"unreadable expiry date {r['expires']!r}")
    if r.get("attribution_required") and not r.get("attribution_text"):
        hard.append("attribution is required but no attribution_text is recorded")
    if pathway == "public_domain" and not rc.get("auto_approve_public_domain", False):
        soft.append("public-domain claim: a human must confirm there is no third-party music/footage inside")
    if THIRD_PARTY.search(" ".join(str(src.get(k, "")) for k in ("description", "title"))):
        soft.append("source description mentions third-party material (©/courtesy/music/licensed)")
    if src.get("platform") == "youtube" and (src.get("media") or {}).get("kind") != "file":
        soft.append("YouTube's Terms forbid downloading; the creator must supply the file (put it in the inbox)")
    human = (src.get("review") or {}).get("human") or {}
    if hard or human.get("verdict") == "rejected":
        status = "rejected"
        if human.get("verdict") == "rejected":
            hard.append(f"rejected by {human.get('reviewer')}: {human.get('note', '')}".strip(": "))
    elif soft and human.get("verdict") != "approved":
        status = "manual_review"
    else:
        status = "approved"
    return {"status": status, "hard": hard, "soft": soft}


def review(cfg, source_id):
    """Runs the gate and stores the decision on the source record (review.status is always recomputed;
    review.human keeps what a person decided)."""
    src = registry.get(cfg, source_id)
    d = evaluate(src, cfg)
    status = {"approved": "eligible", "manual_review": "manual_review", "rejected": "rejected",
              "revoked": "revoked"}[d["status"]]
    if d["status"] == "approved" and src["status"] in ("ingested", "segmented", "exhausted"):
        status = src["status"]  # already further along; a re-check that passes doesn't reset progress
    registry.update(cfg, source_id, status=status, last_reviewed=registry.today(),
                    review={**(src.get("review") or {}), "status": d["status"], "hard": d["hard"], "soft": d["soft"]})
    event("rights_decision", source=source_id, decision=d["status"], hard=d["hard"], soft=d["soft"])
    return d


def _human(cfg, source_id, verdict, reviewer, note):
    src = registry.get(cfg, source_id)
    if not src:
        raise SystemExit(f"no source {source_id}")
    if not reviewer:
        raise SystemExit("a reviewer name is required (--reviewer)")
    registry.update(cfg, source_id, review={**(src.get("review") or {}), "human": {
        "verdict": verdict, "reviewer": reviewer, "note": note, "decided": registry.today()}})
    d = review(cfg, source_id)
    log(f"{source_id}: {d['status']}" + (f" — blocked: {'; '.join(d['hard'])}" if d["hard"] else ""))
    return d


def approve(cfg, source_id, reviewer, note=""):
    """Human decision on a manual_review source. Clears soft failures only: hard failures still reject."""
    return _human(cfg, source_id, "approved", reviewer, note)


def reject(cfg, source_id, reviewer, note=""):
    return _human(cfg, source_id, "rejected", reviewer, note)


def revoke(cfg, source_id, reason):
    """Permission withdrawn or licence ended: no further processing, queued Shorts from it are pulled
    (quarantined), and live ones are flagged for a take-down decision (deleting is left to a human)."""
    from .. import jobs
    src = registry.get(cfg, source_id)
    if not src:
        raise SystemExit(f"no source {source_id}")
    rights = {**(src.get("rights") or {}), "revoked": True, "revoked_reason": reason, "revoked_on": registry.today()}
    registry.update(cfg, source_id, rights=rights, status="revoked",
                    review={**(src.get("review") or {}), "status": "revoked"},
                    incidents=src.get("incidents", []) + [{"date": registry.today(), "kind": "revoked", "note": reason}])
    pulled, flagged = [], []
    for j in jobs.load():
        if j.get("source_id") != source_id:
            continue
        if j["status"] in ("awaiting_approval", "ready", "dry_run"):
            jobs.mark(j["folder"], status="quarantined", reason=f"source rights revoked: {reason}")
            pulled.append(j["folder"])
        elif j["status"] in ("uploading", "scheduled", "published"):
            jobs.mark(j["folder"], alert=f"source rights revoked ({reason}): decide take-down per the agreement")
            flagged.append(j["folder"])
    event("rights_revoked", source=source_id, reason=reason, pulled=pulled, flagged=flagged)
    log(f"revoked {source_id}: pulled {len(pulled)} queued Short(s), flagged {len(flagged)} live one(s)")
    return pulled, flagged


def is_cleared(cfg, source_id):
    """Re-checked at every step that touches the source (ingest, segment, render, review, publish)."""
    src = registry.get(cfg, source_id)
    return bool(src) and evaluate(src, cfg)["status"] == "approved"
