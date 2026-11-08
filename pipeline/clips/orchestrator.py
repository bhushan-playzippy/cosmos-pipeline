"""Clip-channel orchestrator: discovery → rights → ingest → segments → editorial → render → compliance → queue.
Publishing, reconciliation, status sync, comments and reports reuse pipeline/orchestrator.py and analytics.py, so
both channels share one tested publishing path, each on its own files.

Job records use the same lifecycle as the original channel (jobs.py) plus source_id / segment_id, so the daily
target counts only what YouTube confirms as published.
"""
import datetime as dt
import json
import shutil
import traceback

from . import compliance, discovery, edit, editorial, ingest, package, registry, rights, segments
from .. import control, jobs
from .. import orchestrator as core
from ..util import slugify, write_json, log, event


def _post_txt(folder, script):
    s, src, r = script["segment"], script["source"], script["source"].get("rights") or {}
    cmp = script.get("compliance", {})
    lines = ["=== YOUTUBE ===", "TITLE:", script["youtube"]["title"], "", "DESCRIPTION:", script["youtube"]["description"],
             "", "TAGS:", ", ".join(script["youtube"]["tags"]), "", "PINNED COMMENT:", script["youtube"]["pinned_comment"],
             "", "=== COMPLIANCE ===", "PASS" if cmp.get("pass") else "FAIL",
             *[f"  [{'ok' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}" for c in cmp.get("checks", [])], "",
             "=== SOURCE & RIGHTS ===", f"{src['id']}  {src.get('title')} — {src.get('creator')}", src.get("url") or "",
             f"pathway: {r.get('pathway')}  licence: {r.get('licence')}  holder: {r.get('holder')}",
             f"evidence: {r.get('evidence')}", f"commercial use: {r.get('commercial_use')}  expires: {r.get('expires')}",
             *[f"restriction: {x}" for x in r.get("restrictions", [])],
             f"review: {(src.get('review') or {}).get('status')}  human: {(src.get('review') or {}).get('human')}", "",
             "=== SEGMENT ===", f"{s['start']:.2f}s – {s['end']:.2f}s  score {s['score']}  type {s['type']}",
             f"why: {'; '.join(s['why'])}", f"scores: {s['scores']}", "",
             "=== NARRATION (ours) / CLIP (theirs) ===", f"INTRO: {script['narration']['intro']}",
             f"CLIP:  \"{s['text']}\"", f"OUTRO: {script['narration']['outro']}", "",
             "=== EDIT ===", json.dumps(script["edit"], indent=1)]
    (folder / "post.txt").write_text("\n".join(lines), encoding="utf-8")


def prepare_candidates(cfg, need):
    """Ingests and segments the best cleared sources until `need` candidates exist (bounded per run)."""
    have = [s for s in registry.segments(cfg) if s["status"] == "candidate_selected"]
    budget = cfg["ingest"].get("max_sources_per_run", 3)
    srcs = sorted(registry.sources(cfg).values(), key=lambda s: -s.get("score", 0))
    for src in srcs:
        if len(have) >= need or budget <= 0:
            break
        if src["status"] not in ("eligible", "ingested") or not rights.is_cleared(cfg, src["id"]):
            continue
        try:
            if src["status"] == "eligible":
                budget -= 1
                ingest.ingest(cfg, src["id"])
            have += segments.find(cfg, src["id"])
        except Exception as e:
            log(f"{src['id']}: preparation failed: {type(e).__name__}: {e}")
            event("prepare_failed", source=src["id"], error=f"{type(e).__name__}: {e}"[:300])
    return have


def make_short(cfg, slot=None, segment_id=None):
    """Makes one Short from the best candidate (or a given segment). Returns the job record, or None if there was
    no cleared candidate. Never raises for content problems: they end as failed/quarantined jobs."""
    role = jobs.role_for(cfg, slot) if slot else None
    seg = (next((s for s in registry.segments(cfg) if s["id"] == segment_id), None) if segment_id
           else segments.next_candidate(cfg, role))
    if not seg:
        return None
    src = registry.get(cfg, seg["source_id"])
    base = f"{jobs.now():%Y-%m-%d_%H%M%S}_{slugify(src['title'], 36)}-{int(seg['start'])}s"
    folder, n = core.OUTPUT / base, 1
    while folder.exists() or jobs.find(jobs.load(), folder.name):  # never reuse a folder: it would overwrite a job
        n += 1
        folder = core.OUTPUT / f"{base}-{n}"
    work = folder / "_work"
    work.mkdir(parents=True, exist_ok=True)
    jobs.upsert({"folder": folder.name, "topic": src["title"], "status": "failed", "stage": "rights", "role": role,
                 "slot": slot.isoformat() if slot else None, "created": jobs.stamp(), "stages": {},
                 "youtube_id": None, "source_id": src["id"], "segment_id": seg["id"]})
    registry.mark_segment(cfg, seg["id"], status="in_production")
    stage = "rights"
    try:
        if not rights.is_cleared(cfg, src["id"]):
            raise ingest.UnauthorizedSource(f"{src['id']} lost its rights clearance")
        stage = "editorial"
        narration = editorial.write(seg, src, cfg)
        stage = "package"
        meta = package.package(seg, src, narration, cfg)
        stage = "render"
        video, edit_rec = edit.render(seg, src, narration, cfg, work, folder / "video.mp4")
        control.count("renders")
        stage = "compliance"
        result = compliance.review(video, seg, src, narration, meta, edit_rec, cfg, work, package.used_titles())
        script = {"kind": "clip", "topic": meta["title"], "youtube": meta, "narration": narration,
                  "segment": seg, "source": registry.get(cfg, src["id"]), "edit": edit_rec, "compliance": result,
                  "sources": [src.get("url") or src["id"]],
                  "scenes": [{"text": narration["intro"]}, {"text": f"[clip] {seg['text']}"}, {"text": narration["outro"]}]}
        write_json(folder / "script.json", script)
        _post_txt(folder, script)
        shutil.rmtree(work, ignore_errors=True)
        failed = [c["name"] for c in result["checks"] if not c["ok"]]
        reason = ("compliance: " + ", ".join(failed)) if failed else None
        status = ("quarantined" if reason else "dry_run" if control.DRY_RUN else
                  "awaiting_approval" if cfg.get("review", {}).get("human_approval", True) else "ready")
        registry.mark_segment(cfg, seg["id"], status="rejected" if reason else "used", folder=folder.name)
        registry.update(cfg, src["id"], shorts=registry.get(cfg, src["id"]).get("shorts", []) + [folder.name])
        control.count("videos_quarantined" if reason else "videos_made")
        event("short_made", folder=folder.name, status=status, reason=reason, source=src["id"], segment=seg["id"])
        log(f"{status.upper()} → {folder}" + (f" ({reason})" if reason else ""))
        return jobs.mark(folder.name, status=status, stage="gates", reason=reason, title=meta["title"])
    except SystemExit:
        registry.mark_segment(cfg, seg["id"], status="candidate_selected")
        raise
    except Exception as e:
        rejected = isinstance(e, (editorial.NotFeasible, ingest.UnauthorizedSource))
        tries = seg.get("errors", 0) + 1
        registry.mark_segment(cfg, seg["id"], errors=tries, last_error=f"{type(e).__name__}: {e}"[:200],
                              status="rejected" if rejected or tries >= 2 else "candidate_selected")
        log(f"FAILED at stage '{stage}': {e}")
        (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
        event("short_failed", folder=folder.name, stage=stage, error=f"{type(e).__name__}: {e}"[:300])
        return jobs.mark(folder.name, status="failed", stage_failed=stage, reason=f"{type(e).__name__}: {e}"[:300])


def produce(cfg, max_videos=None):
    """Fills free slots over schedule.buffer_days. Fewer cleared candidates than slots = a recorded shortfall,
    never a skipped gate."""
    max_videos = max_videos or cfg["schedule"].get("max_videos_per_run", 5)
    made = 0
    while made < max_videos and not control.killed(cfg):
        if control.gemini_budget_left(cfg) < cfg["control"].get("gemini_calls_per_video", 4):
            log("not enough model budget left this run for another Short")
            break
        earliest = None
        if cfg.get("review", {}).get("human_approval", True):
            earliest = jobs.now() + dt.timedelta(hours=cfg["review"].get("approval_lead_hours", 12))
        free = jobs.free_slots(cfg, jobs.load(), cfg["schedule"].get("buffer_days", 2), earliest=earliest)
        if not free:
            log("all slots in the buffer window are filled")
            break
        prepare_candidates(cfg, need=len(free))
        job = make_short(cfg, slot=free[0])
        if job is None and cfg.get("stats", {}).get("enabled"):
            from . import stats  # no permission-cleared clip: an original creator-stats Short instead
            job = stats.make(cfg, slot=free[0])
        if job is None:
            log(f"SHORTFALL: no rights-cleared candidate for {free[0]:%a %d %b %H:%M}; {len(free)} slot(s) open")
            event("shortfall", open_slots=len(free))
            break
        made += 1
        if job["status"] == "failed" and job.get("stage_failed") == "editorial" and "budget" in (job.get("reason") or ""):
            break


def daily(cfg):
    """What the scheduler runs for this channel. Each step is independent: one failing step doesn't block the rest."""
    if control.killed(cfg):
        return
    steps = (("sync", core.sync_status), ("catch-up uploads", core.upload_pending), ("discovery", discover_if_needed),
             ("production", produce), ("uploads", core.upload_pending), ("comments", core.engage),
             ("notify", core.notify))
    for name, step in steps:
        if control.killed(cfg):
            break
        try:
            log(f"--- {name}")
            step(cfg)
        except SystemExit as e:
            log(f"{name} stopped: {e}")
            core.login_alert(cfg, e)
        except Exception as e:
            log(f"{name} failed: {type(e).__name__}: {e}")
            event("step_failed", step=name, error=f"{type(e).__name__}: {e}"[:300])
    try:
        from .. import analytics
        analytics.snapshot(cfg)
        from ..growth import analyse
        analyse(cfg)
        analytics.write_daily_report(cfg)
    except SystemExit as e:
        log(f"analytics stopped: {e}")
    except Exception as e:
        log(f"analytics failed: {type(e).__name__}: {e}")


def discover_if_needed(cfg):
    """Discovers more only when the cleared pipeline is thinner than discovery.min_cleared_candidates."""
    cands = [s for s in registry.segments(cfg) if s["status"] == "candidate_selected"]
    eligible = [s for s in registry.sources(cfg).values() if s["status"] in ("eligible", "ingested")]
    if len(cands) + 2 * len(eligible) >= cfg["discovery"].get("min_cleared_candidates", 15):
        log(f"discovery not needed ({len(cands)} candidates, {len(eligible)} cleared sources waiting)")
        return
    discovery.run(cfg)


def report_lines(cfg):
    """Clip-channel section of the daily dashboard: rights funnel, review queue, production, shortfall, cost."""
    srcs = registry.sources(cfg)
    segs = registry.segments(cfg)
    by = {}
    for s in srcs.values():
        by[s["status"]] = by.get(s["status"], 0) + 1
    decided = sum(by.get(k, 0) for k in ("eligible", "ingested", "segmented", "exhausted", "rejected", "revoked"))
    rej = by.get("rejected", 0) + by.get("revoked", 0)
    lines = ["## Sources and rights", f"- Registry: {len(srcs)} sources · " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())),
             f"- Rights rejection rate: {rej}/{decided}" + (f" ({rej / decided:.0%})" if decided else ""), ""]
    queue = sorted((s for s in srcs.values() if s["status"] == "manual_review"), key=lambda s: -s.get("score", 0))
    if queue:
        lines += [f"### Rights review queue ({len(queue)}) — top 8 by score",
                  "Check the source (third-party music/footage, people, restrictions), then approve or reject:", ""]
        for s in queue[:8]:
            lines += [f"- `{s['id']}` {s.get('title', '')[:70]} — {'; '.join((s.get('review') or {}).get('soft', []))[:160]}",
                      f"  `python main.py --channel {cfg['channel']['id']} rights-approve '{s['id']}' --reviewer <you>`"]
        lines.append("")
    seg_by = {}
    for s in segs:
        seg_by[s["status"]] = seg_by.get(s["status"], 0) + 1
    lines += ["## Segments", "- " + (", ".join(f"{k} {v}" for k, v in sorted(seg_by.items())) or "none yet"), ""]
    ev = []
    p = cfg["_paths"]["events"]
    if p.exists():
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat()
        ev = [e for e in map(json.loads, p.read_text().splitlines()) if e["ts"] >= cutoff]
    ups = sum(1 for e in ev if e["event"] == "uploaded")
    fails = sum(1 for e in ev if e["event"] == "upload_failed")
    made = sum(1 for e in ev if e["event"] == "short_made" and not e.get("reason"))
    quar = sum(1 for e in ev if e["event"] == "short_made" and e.get("reason"))
    lines += ["## Last 7 days", f"- Shorts passing every gate: {made} · quarantined: {quar} · "
              f"production failures: {sum(1 for e in ev if e['event'] == 'short_failed')} · "
              f"shortfall events: {sum(1 for e in ev if e['event'] == 'shortfall')}",
              f"- Upload success: {ups}/{ups + fails}" if ups + fails else "- Upload success: no uploads yet", ""]
    camp = {}
    for j in jobs.load():
        if j.get("source_id") and j["status"] in jobs.LIVE and j.get("youtube_id"):
            r = (registry.get(cfg, j["source_id"]) or {}).get("rights") or {}
            if r.get("pathway") == "clip_campaign":
                camp.setdefault(r.get("campaign", "?"), []).append(j)
    if camp:
        lines += ["## Campaign submissions", "Most campaigns pay only for links you submit. Published/scheduled Shorts:", ""]
        for name, js in sorted(camp.items()):
            lines.append(f"- **{name}**")
            lines += [f"  - {j['status']} {j.get('publish_at', '')[:16]} https://youtube.com/shorts/{j['youtube_id']}"
                      for j in js]
        lines.append("")
    return lines
