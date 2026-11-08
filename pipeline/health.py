"""Autonomous monitoring: alerts the owner (macOS banner + phone via ntfy, orchestrator.alert) only when something
needs a person, and keeps disk use bounded. Runs at the end of every daily run; each alert is sent at most once a
day (alerts.json next to history.json), so an unattended week doesn't become a wall of pushes.

Alerts (thresholds in `control`):
  no publication       nothing published or scheduled for alert_no_publish_days (3)
  quality              the last quality_failure_streak (3) jobs all failed or were quarantined for content reasons
  provider outage      a provider tripped its circuit breaker, or 3+ deferrals for overload, in 24 h
  upload failure       any upload_failed event in 24 h
  missed runs          runs.py recorded scheduled runs that never started, or an interrupted run
  other channel stale  another channel's last completed run is older than stale_run_hours (26): its scheduler may
                       be dead, which that channel can't report itself
  disk                 free space below min_free_gb (10)
Retention (prune): rendered files of failed / quarantined / rejected / deleted / dry-run jobs older than
retention_days (14) and leftover _work folders are deleted; script.json, post.txt and error.txt stay (audit
records). Ready, waiting, uploading, scheduled and published videos are never touched.
"""
import datetime as dt
import json
import shutil

from . import jobs, util
from .util import log, read_json, write_json

PRUNABLE = ("failed", "quarantined", "rejected", "deleted", "dry_run")
KEEP = ("script.json", "post.txt", "error.txt")  # + verified*.json: the immutable claim-check records


def _events(hours):
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()
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


def checks(cfg, now=None):
    """[(key, message)] for every condition that needs the owner now."""
    c = cfg.get("control", {})
    now = now or jobs.now()
    all_jobs = jobs.load()
    out = []
    live = [jobs.when(j) for j in all_jobs if j["status"] in ("scheduled", "published") and jobs.when(j)]
    days = c.get("alert_no_publish_days", 3)
    last = max(live) if live else None
    if not last or now - last > dt.timedelta(days=days):
        out.append(("no_publication", f"no video published or scheduled for {days}+ days "
                                      f"(last: {last:%d %b %H:%M} IST)" if last else "no video ever published"))
    streak = c.get("quality_failure_streak", 3)
    recent = sorted((j for j in all_jobs if j.get("created")), key=lambda j: j["created"])[-streak:]
    from .quality import classify
    if len(recent) == streak and all(classify(j) == "content" for j in recent):
        out.append(("quality", f"the last {streak} videos all failed quality gates (latest: "
                               f"{(recent[-1].get('reason') or '')[:80]})"))
    ev = _events(24)
    tripped = sorted({e.get("provider") for e in ev if e.get("event") == "provider_tripped"})
    overload = sum(1 for e in ev if e.get("event") == "deferred" and "overload" in str(e.get("cause")))
    if tripped or overload >= 3:
        out.append(("provider", f"model providers unreliable in the last 24 h: tripped {', '.join(tripped) or '-'}, "
                                f"{overload} overload deferral(s)"))
    ups = [e for e in ev if e.get("event") == "upload_failed"]
    if ups:
        out.append(("upload", f"{len(ups)} upload failure(s) in 24 h: {str(ups[-1].get('error'))[:80]}"))
    missed = [e for e in ev if e.get("event") in ("missed_runs", "interrupted_run")]
    if missed:
        out.append(("missed_runs", f"{len(missed)} missed or interrupted scheduled run(s) in 24 h (Mac asleep/off?)"))
    out += _stale_channels(cfg, now)
    free = shutil.disk_usage(util.ROOT).free / 1e9
    if free < c.get("min_free_gb", 10):
        out.append(("disk", f"only {free:.1f} GB free on the disk"))
    return out


def _stale_channels(cfg, now):
    from . import channels
    hours = cfg.get("control", {}).get("stale_run_hours", 26)
    out = []
    for cid in channels.available():
        if cid == cfg["channel"]["id"]:
            continue
        try:
            path = channels.load(cid)["_paths"]["data"] / "runs.json"
            last = read_json(path, {}).get("last_ok")
        except Exception:  # an unreadable profile is reported by `main.py channels`, not here
            continue
        if last and now - dt.datetime.fromisoformat(last) > dt.timedelta(hours=hours):
            out.append((f"stale_{cid}", f"channel '{cid}' hasn't completed a run for {hours}+ h (last {last[:16]}): "
                                        "is its scheduler loaded?"))
    return out


def notify(cfg, now=None):
    """Sends each current alert at most once a day per channel. Returns the alerts found."""
    from . import control
    from .orchestrator import alert
    found = checks(cfg, now)
    if control.DRY_RUN:  # a rehearsal reports but sends and stores nothing
        for _key, msg in found:
            log(f"health (dry run, not sent): {msg}")
        return found
    path = jobs.HISTORY.parent / "alerts.json"
    sent = read_json(path, {})
    today = (now or jobs.now()).date().isoformat()
    for key, msg in found:
        util.event("health_alert", key=key, message=msg)
        if sent.get(key) != today:
            alert(cfg, msg)
            sent[key] = today
    write_json(path, {k: v for k, v in sent.items() if v >= (dt.date.fromisoformat(today) - dt.timedelta(days=30)).isoformat()})
    for key, msg in found:
        log(f"health: {msg}")
    return found


def _born(job):
    """When the job was made: its `created` stamp, else the date its folder name starts with, else None."""
    import re
    if job.get("created"):
        return str(job["created"])[:19]
    m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})", job.get("folder", ""))
    return f"{m.group(1)}T{m.group(2)}:{m.group(3)}:00" if m else None


def prune(cfg, now=None, dry=None):
    """Frees disk from videos that will never upload. Returns (paths removed or listed, bytes)."""
    from . import control
    dry = control.DRY_RUN if dry is None else dry
    days = cfg.get("control", {}).get("retention_days", 14)
    cutoff = ((now or jobs.now()) - dt.timedelta(days=days)).isoformat()[:19]
    freed, gone = 0, []
    for j in jobs.load():
        d = jobs.OUTPUT / j["folder"]
        if not d.exists():
            continue
        targets = []
        born = _born(j)
        if born is None or born >= cutoff:  # unknown age is never old (2 Nov: jobs without `created` were pruned)
            continue
        if j["status"] in PRUNABLE:
            targets = [p for p in d.iterdir() if p.name not in KEEP and not p.name.startswith("verified")]
        elif (d / "_work").exists() and j["status"] not in ("ready", "uploading", "awaiting_approval"):
            targets = [d / "_work"]
        for p in targets:
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
            freed += size
            gone.append(str(p))
            if not dry:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    if gone:
        log(f"retention: {'would free' if dry else 'freed'} {freed / 1e6:.0f} MB from {len(gone)} file(s) of jobs that "
            "will never upload (audit records kept)")
        if not dry:
            util.event("retention", files=len(gone), bytes=freed)
    return gone, freed


def report_lines(cfg):
    found = checks(cfg)
    usage = shutil.disk_usage(util.ROOT)
    return ["## Health", *(f"- ALERT: {m}" for _k, m in found), *([] if found else ["- no alerts"]),
            f"- Disk: {usage.free / 1e9:.1f} GB free of {usage.total / 1e9:.0f} GB", ""]
