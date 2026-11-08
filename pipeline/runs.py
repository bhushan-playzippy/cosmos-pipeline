"""Run continuity for the daily scheduler: what happened to the previous run, which scheduled runs were missed,
and whether the network is up before any API is touched.

Why: the Mac is a laptop. 28 Oct the 21:40 clips run stopped logging mid-production (asleep ~24 h) and left a
"failed" placeholder with no reason; 29 and 31 Oct runs started with no DNS and spent their steps failing one by
one. launchd already runs a calendar job missed during sleep once on wake, and RunAtLoad (in the plists) runs one
at login after a restart; this module makes those runs safe and visible:
  - start(): records the run in runs.json (per channel: it sits next to history.json); a previous run that never
    finished is logged as interrupted, and its unfinished placeholder jobs get a reason instead of a blank;
  - missed(): scheduled run times (control.run_times_ist) since the last run that never started, logged as a
    "missed_runs" event, so the daily report shows them;
  - too_soon(): a RunAtLoad/wake run right after a completed run is skipped (control.min_minutes_between_runs);
  - wait_for_network(): a few bounded checks (control.network_checks × network_check_seconds); still offline →
    the run is skipped and recorded, never retried in a loop. The next scheduled run is the retry.
Overlap is already impossible: main.py holds the per-channel flock for the whole run.
"""
import datetime as dt
import socket
import time

from . import control, jobs
from .util import event, log, read_json, write_json


def _file():
    return jobs.HISTORY.parent / "runs.json"


def _parse(s):
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def start(cfg):
    """Call at the top of daily. Returns the previous state (for missed())."""
    state = read_json(_file(), {})
    cur = state.get("current")
    if cur and not cur.get("finished"):
        started = _parse(cur.get("started"))
        log(f"previous run (started {cur.get('started')}) never finished: interrupted (sleep, crash or watchdog)")
        event("interrupted_run", started=cur.get("started"))
        if started:  # its placeholders: _make_once records "failed" first and fills in the result at the end
            for j in jobs.load():
                made = _parse(j.get("created"))
                if (j["status"] == "failed" and not j.get("reason") and made and made >= started):
                    jobs.mark(j["folder"], reason="interrupted: the run ended before this video finished "
                                                  "(sleep/crash); nothing was uploaded", stage_failed=j.get("stage"))
    prev = dict(state)
    if not control.DRY_RUN:
        state["current"] = {"started": jobs.stamp(), "finished": None}
        write_json(_file(), state)
    return prev


def finish(cfg, outcome="ok"):
    if control.DRY_RUN:
        return
    state = read_json(_file(), {})
    cur = state.get("current") or {"started": jobs.stamp()}
    cur.update(finished=jobs.stamp(), outcome=outcome)
    state["current"] = cur
    if outcome == "ok":
        state["last_ok"] = cur["finished"]
    write_json(_file(), state)


def missed(cfg, prev, now=None):
    """Scheduled run times (IST, control.run_times_ist) between the previous run's start and now, other than the
    one this run is serving (within 90 min), for which no run started."""
    times = cfg.get("control", {}).get("run_times_ist", [])
    last = _parse((prev.get("current") or {}).get("started"))
    now = now or jobs.now()
    if not times or not last:
        return []
    out, day = [], last.date()
    while day <= now.date():
        for t in times:
            h, m = map(int, t.split(":"))
            due = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=jobs.IST)
            if last + dt.timedelta(minutes=5) < due and due < now - dt.timedelta(minutes=90):
                out.append(due.isoformat(timespec="minutes"))
        day += dt.timedelta(days=1)
    if out:
        log(f"missed {len(out)} scheduled run(s) (Mac asleep/off?): {', '.join(out)}")
        event("missed_runs", runs=out)
    return out


def too_soon(cfg, prev, now=None):
    gap = cfg.get("control", {}).get("min_minutes_between_runs", 45)
    last = _parse(prev.get("last_ok"))
    return bool(gap and last and (now or jobs.now()) - last < dt.timedelta(minutes=gap))


def online(hosts=("oauth2.googleapis.com", "www.googleapis.com"), timeout=5):
    try:
        for h in hosts:
            socket.create_connection((h, 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def wait_for_network(cfg):
    c = cfg.get("control", {})
    checks, pause = c.get("network_checks", 4), c.get("network_check_seconds", 60)
    for n in range(checks):
        if online():
            return True
        if n < checks - 1:
            log(f"no network (check {n + 1}/{checks}); waiting {pause}s")
            time.sleep(pause)
    log("still offline: skipping this run (the next scheduled run retries; nothing was started)")
    event("offline_skip", checks=checks)
    return False
