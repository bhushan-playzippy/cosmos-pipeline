"""Quality and operations report: what the content gates decided over the last N days, per channel, with every
outcome put in one of four classes so a quiet day reads correctly:
  content      the video's own words or pictures failed (verify, QA, grounding, reviewer, too-short script)
  infra        the machine or a service failed (interrupted run, render/FFmpeg, network, server errors)
  quota        no model capacity (deferred to the next run; nothing wrong with the topic)
  human        a person decided (rejected, withdrawn, awaiting approval)
Why: a dry run that "exits 0" said nothing about whether videos could be made (1 Nov: both channels deferred
every topic), and 13 clips withdrawals never showed up as a rate anywhere. Reads history.json, events.jsonl,
each job's script.json (claim ledger, visual_grounding) and the channel snapshot CSV. No model calls.
"""
import csv
import datetime as dt
import json
import re

from . import jobs, util

QUOTA = re.compile(r"quota|429|perday|budget|deferr|no strong", re.I)
INFRA = re.compile(r"interrupt|timed? ?out|50[0234]|connection|network|unreachable|ffmpeg|errno|render|"
                   r"disconnected|no such file|watchdog", re.I)


def classify(job):
    st, why = job.get("status"), f"{job.get('reason') or ''} {job.get('stage_failed') or ''}"
    if st in ("rejected", "withdrawn", "awaiting_approval"):
        return "human"
    if st not in ("failed", "quarantined"):
        return None
    if QUOTA.search(why):
        return "quota"
    if job.get("stage_failed") == "render" or (st == "failed" and INFRA.search(why)):
        return "infra"
    return "content"


def _events(since):
    out = []
    try:
        with open(util.EVENTS, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("ts", "") >= since:
                    out.append(e)
    except OSError:
        pass
    return out


def collect(days=7):
    from . import llm
    now = jobs.now()
    start = now - dt.timedelta(days=days)
    recent = [j for j in jobs.load() if (j.get("created") or "") >= start.isoformat()[:19]]
    deferred = [e for e in _events(start.astimezone(dt.timezone.utc).isoformat()) if e.get("event") == "deferred"]
    c = {"days": days, "topics_attempted": len(recent) + len(deferred), "deferred_by_stage": {},
         "scripts_generated": 0, "scripts_verified": 0, "claims": {}, "visual": {}, "audio_qa_failures": 0,
         "render_failures": 0, "reviewer_rejections": 0, "grounding_failures": 0, "ready": 0, "scheduled": 0,
         "published": 0, "topics_rejected": 0,
         "classes": {"content": 0, "infra": 0, "quota": len(deferred), "human": 0}}
    c["deferred_by_cause"] = {}
    for e in deferred:
        why = e.get("cause") or "unrecorded (before 2 Nov)"
        c["deferred_by_cause"][why] = c["deferred_by_cause"].get(why, 0) + 1
        k = e.get("stage") or "?"
        c["deferred_by_stage"][k] = c["deferred_by_stage"].get(k, 0) + 1
    for j in recent:
        why = j.get("reason") or ""
        k = classify(j)
        if k:
            c["classes"][k] += 1
        c["topics_rejected"] += j["status"] in ("failed", "quarantined", "rejected")
        c["ready"] += j["status"] == "ready"
        c["scheduled"] += j["status"] == "scheduled"
        c["published"] += j["status"] == "published"
        c["render_failures"] += j.get("stage_failed") == "render"
        c["reviewer_rejections"] += why.startswith("AI reviewer")
        c["grounding_failures"] += "visual grounding" in why
        c["audio_qa_failures"] += bool(re.search(r"loudness|silence|music audible|audio covers", why))
        c["scripts_generated"] += not (j["status"] == "failed" and j.get("stage_failed") in ("script", None))
        try:
            s = jobs.script_of(j)
        except (OSError, ValueError):
            continue
        c["scripts_verified"] += bool(s.get("claims_for"))
        for cl in s.get("claims") or []:
            st = cl.get("status") or "no status (pre-1 Nov ledger)"
            c["claims"][st] = c["claims"].get(st, 0) + 1
        for r in s.get("visual_grounding") or []:
            c["visual"][r["decision"]] = c["visual"].get(r["decision"], 0) + 1
    n = sum(c["visual"].values())
    c["visual_rates"] = {k: round(v / n, 2) for k, v in c["visual"].items()} if n else {}
    made = c["ready"] + c["scheduled"] + c["published"]
    c["success_rate"] = round(made / c["topics_attempted"], 2) if c["topics_attempted"] else None
    since = start.astimezone(dt.timezone.utc).isoformat()
    ev = _events(since)
    c["recovery"] = {k: sum(1 for e in ev if e.get("event") == k) for k in
                     ("draft_saved", "draft_resumed", "provider_tripped", "release_decision", "upload_reconciled",
                      "retention")}
    c["subscribers_gained"] = _subs(days)
    c["usage_today"] = llm.usage_today()
    return c


def _subs(days):
    from . import analytics
    try:
        with open(analytics.ADIR / "channel.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return None
    cut = (jobs.now() - dt.timedelta(days=days)).date().isoformat()
    window = [r for r in rows if r["date"] >= cut] or rows[-1:]
    try:
        return int(window[-1]["subscribers"]) - int(window[0]["subscribers"])
    except (ValueError, KeyError, IndexError):
        return None


def report_lines(cfg, days=7):
    c = collect(days)
    cl, vr = c["classes"], c["visual_rates"]
    subs = c["subscribers_gained"]
    lines = [f"## Content quality and operations (last {days} days)",
             f"- Topics attempted {c['topics_attempted']} · rejected {c['topics_rejected']} · scripts generated "
             f"{c['scripts_generated']} · verified {c['scripts_verified']}",
             f"- Outcomes: ready {c['ready']} · scheduled {c['scheduled']} · published {c['published']} · "
             f"subscribers gained {subs if subs is not None else 'n/a'}",
             f"- Not made, by class: content {cl['content']} · infrastructure {cl['infra']} · quota (deferred) "
             f"{cl['quota']} {c['deferred_by_stage'] or ''} · human review {cl['human']}",
             "- Deferral causes: " + (", ".join(f"{k} {v}" for k, v in sorted(c["deferred_by_cause"].items())) or "none"),
             "- Claims: " + (", ".join(f"{k} {v}" for k, v in sorted(c["claims"].items())) or "none recorded"),
             "- Visual grounding: " + (", ".join(f"{k} {v:.0%}" for k, v in sorted(vr.items()))
                                       + f" of {sum(c['visual'].values())} scenes" if vr else "no grounded videos yet"),
             f"- Gate failures: reviewer {c['reviewer_rejections']} · grounding {c['grounding_failures']} · audio QA "
             f"{c['audio_qa_failures']} · render {c['render_failures']}"]
    lines.append(f"- Pipeline success rate: {c['success_rate']:.0%} of attempted topics became videos"
                 if c["success_rate"] is not None else "- Pipeline success rate: no attempts")
    lines.append("- Recovery actions: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in c["recovery"].items() if v)
                 if any(c["recovery"].values()) else "- Recovery actions: none")
    from . import llm
    gone = sorted(llm.EXHAUSTED | set(llm.read_json(llm.QUOTA_FILE, {}).get(llm._quota_day(), {}).get(
        llm._fp(cfg), [])))
    limits = (cfg.get("gemini") or {}).get("daily_limits") or {}
    lines.append("- Quota today: " + (f"exhausted {', '.join(gone)}" if gone else "no model exhausted")
                 + ("; limits " + ", ".join(f"{m} {c['usage_today'].get(m, {}).get('calls', 0)}/{n}" for m, n in limits.items())
                    if limits else "") + " · paid spend ₹0 (free tiers only)")
    if c["usage_today"]:
        lines.append("- Model requests today (Pacific day): " + ", ".join(
            f"{m} {u['calls']}" + (f" ({u['failed']} failed)" if u["failed"] else "")
            for m, u in sorted(c["usage_today"].items())))
    return lines + ["- A dry run that exits 0 only shows the run is safe; production is the ready/scheduled count.", ""]


def inspect(folder):
    """Readable claim ledger + visual grounding + verification state of one video."""
    from . import grounding, verify
    j = jobs.find(jobs.load(), folder) or {"folder": folder}
    s = jobs.script_of(j)
    ok, how = verify.verified(s)
    out = [f"{folder}: status {j.get('status', '?')}", f"verification: {'OK' if ok else 'NOT VERIFIED'}: {how}",
           f"independence: {s.get('independence') or s.get('provenance') or 'not recorded'}", "", "CLAIMS"]
    for c in s.get("claims") or []:
        cite = c.get("citation") or {}
        src = (f"{cite.get('quality')} {cite.get('kind')}: {cite.get('url')}" if cite else
               f"{c.get('evidence_type', 'model')}: {c.get('evidence_source') or c.get('source') or '-'}")
        out.append(f"  [{c.get('status', '?'):<19}] s{c.get('scene')} {c.get('category') or c.get('type', '')}: "
                   f"{str(c.get('claim'))[:90]}\n      {src}")
    recs = s.get("visual_grounding")
    out += ["", "VISUAL GROUNDING" + ("" if recs else " (not recorded at make time: deterministic view now)")]
    if not recs:
        recs = grounding.records(s.get("scenes", []), s, True)
    for r in recs:
        a = r["asset"]
        out.append(f"  scene {r['scene']} [{r['decision']}] \"{r['narration'][:70]}\"\n      {a.get('source')} "
                   f"{a.get('id')} {str(a.get('title'))[:70]} ({a.get('date') or 'no date'}): "
                   f"{'; '.join(r['reasons'])[:160]}")
    return "\n".join(out)
