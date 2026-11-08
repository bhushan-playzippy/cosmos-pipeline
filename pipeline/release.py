"""Automated release decision: the gates a video must pass before it may upload, checked from what the
pipeline recorded about it (script.json, the job, the video file) at the moment of release.

Why: the owner authorised fully autonomous publishing (2 Nov), so the "human looks before upload" step is replaced
by a decision that fails closed: any gate that fails, or whose evidence is missing (an older video made before a
gate existed, a check that never ran), blocks the upload and quarantines the video. Nothing here relaxes a gate;
each one re-reads the result of an existing stage:
  verified text     verify.verified (the claim ledger covers the narration and every visible text)
  claims            the ledger exists and no claim is a failure
  qa                the deterministic QA ran and every check passed (technical, editorial, originality)
  review            the AI reviewer ran (when enabled) and passed
  visuals           per-scene grounding recorded; no MISMATCH/UNVERIFIED; MATCH share and reuse limits hold
  rights            every scene has a source and licence, archive/stock scenes an asset id
  metadata          title (≤ 100 chars), description, tags and the AI disclosure are present
  video file        the rendered file exists and isn't empty
  duplicates        no other live video of this channel has the same title
Channel ownership is checked at upload (publish_youtube.check_channel / ChannelMismatch), after this decision.
"""
from . import grounding, jobs, verify


def check(job, script, cfg):
    """(ok, [(name, ok, detail)]). ok only when every gate passed."""
    out = []
    add = lambda name, ok, detail="": out.append((name, bool(ok), detail))
    generated = not job.get("source_id")
    if generated and cfg.get("verify", {}).get("enabled", True):
        ok, how = verify.verified(script)
        add("verified text", ok, how)
        claims = script.get("claims") or []
        bad = [c for c in claims if c.get("decision") == "fail"]
        add("claims", claims and not bad, f"{len(claims)} claims" if not bad else
            f"{len(bad)} failing claim(s): {str(bad[0].get('claim'))[:60]}" if claims else "no claim ledger")
    qa = script.get("qa") or {}
    failed_qa = [c["name"] for c in qa.get("checks", []) if not c.get("ok")]
    add("qa", qa.get("checks") and qa.get("pass") and not failed_qa,
        "QA never ran" if not qa.get("checks") else ", ".join(failed_qa) or f"{len(qa['checks'])} checks passed")
    if cfg.get("review", {}).get("enabled", True):
        rv = script.get("review") or {}
        add("review", rv.get("pass") is True, rv.get("summary", "") if rv else "review never ran")
    recs = script.get("visual_grounding")
    if generated:
        if not recs:
            add("visuals", False, "no per-scene visual grounding recorded (made before the gate existed)")
        else:
            issues = grounding.gate(recs, cfg)
            add("visuals", not issues, "; ".join(issues)[:200] or grounding.stats(recs))
    scenes = script.get("scenes", [])
    unrighted = [i for i, sc in enumerate(scenes) if not (sc.get("rights") or {}).get("source")
                 or not (sc.get("rights") or {}).get("license")
                 or ((sc.get("rights") or {}).get("source") in ("commons", "nasa", "pexels", "pixabay")
                     and not (sc.get("rights") or {}).get("asset_id"))]
    add("rights", scenes and not unrighted, f"scenes without provenance: {unrighted}" if unrighted else "")
    yt = script.get("youtube") or {}
    note = (cfg.get("disclosure") or {}).get("text", "")[:30]
    missing = [k for k, ok in (("title", 0 < len(yt.get("title", "")) <= 100), ("description", yt.get("description")),
                               ("tags", yt.get("tags")),
                               ("AI disclosure", not cfg.get("disclosure", {}).get("ai_label", True)
                                or note in yt.get("description", ""))) if not ok]
    add("metadata", not missing, ", ".join(missing))
    video = jobs.OUTPUT / job["folder"] / "video.mp4"  # the active channel's output
    add("video file", video.exists() and video.stat().st_size > 0, "" if video.exists() else "missing")
    title = (yt.get("title") or "").strip().lower()
    twin = next((j for j in jobs.load() if j["folder"] != job["folder"] and j["status"] in jobs.LIVE
                 and (j.get("title") or "").strip().lower() == title), None)
    add("duplicates", not twin, f"same title as {twin['folder']}" if twin else "")
    return all(ok for _n, ok, _d in out), out


def summary(checks):
    return "; ".join(f"{n}: {d}" if d else n for n, ok, d in checks if not ok)
