"""Durable job records (data/history.json) and the publishing-slot scheduler.

One record per video. Lifecycle (only the last two count toward the daily publishing target):
    failed | quarantined          ← stopped before upload, kept for review / recovery
    awaiting_approval             ← passed every gate; waits for a human (review.human_approval), holds its slot
    rejected                      ← a human turned it down; slot freed
    ready                         ← passed every gate (and approval), waiting for an upload slot or quota
    uploading                     ← videos.insert started; if the run dies here, reconcile with YouTube before retrying
    scheduled                     ← uploaded private with publishAt, verified by reading it back from YouTube
    published                     ← YouTube confirms it is public
    deleted                       ← removed from YouTube (reason recorded)
"""
import datetime as dt
import json

from .util import DATA, OUTPUT, read_json, write_json

HISTORY = DATA / "history.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TZ = IST  # the active channel's audience timezone (channels.activate sets it from schedule.timezone)
LIVE = ("scheduled", "published")


def now():
    return dt.datetime.now(TZ)


def stamp():
    return now().isoformat(timespec="seconds")


def load():
    jobs = read_json(HISTORY, [], strict=True)
    for j in jobs:  # records written before job states existed
        if "status" not in j:
            j["status"] = ("deleted" if j.get("deleted") else
                           "scheduled" if j.get("youtube_id") else "ready")
    return jobs


def save(jobs):
    write_json(HISTORY, jobs)


def find(jobs, folder):
    return next((j for j in jobs if j["folder"] == folder), None)


def upsert(job):
    jobs = load()
    old = find(jobs, job["folder"])
    if old:
        old.update(job)
    else:
        jobs.append(job)
    save(jobs)
    return job


def mark(folder, status=None, stage=None, **fields):
    jobs = load()
    j = find(jobs, folder)
    if not j:
        return None
    if status:
        j["status"] = status
    if stage:
        j.setdefault("stages", {})[stage] = stamp()
    j.update(fields)
    save(jobs)
    return j


def script_of(job):
    return json.loads((OUTPUT / job["folder"] / "script.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- scheduling

def _slot_times(cfg):
    slots = cfg["schedule"].get("slots") or cfg["schedule"].get("slots_ist", ["09:00", "14:00", "19:00"])
    return [dt.time(*map(int, s.split(":"))) for s in slots]


HOLDING = ("ready", "awaiting_approval", "uploading")  # not live yet, but keep their planned slot


def when(job):
    """Confirmed publish time for live jobs, planned slot for jobs waiting to upload or for approval."""
    t = (job.get("publish_at") if job.get("status") in LIVE else
         job.get("slot") if job.get("status") in HOLDING else None)
    return dt.datetime.fromisoformat(t).astimezone(TZ) if t else None


def taken_times(jobs, exclude=None):
    return sorted(when(j) for j in jobs if when(j) and j["folder"] != exclude)


def free_slots(cfg, jobs, days, exclude=None, earliest=None):
    """Free publishing slots over the next `days` days: at most per_day videos a day, spaced min_gap_hours apart."""
    per_day = cfg["schedule"].get("videos_per_day", 3)
    gap = dt.timedelta(hours=cfg["schedule"].get("min_gap_hours", 3))
    earliest = earliest or now() + dt.timedelta(minutes=cfg["schedule"].get("lead_minutes", 45))
    taken = taken_times(jobs, exclude)
    out = []
    for d in range(days + 1):
        day = (now() + dt.timedelta(days=d)).date()
        today = [t for t in taken + out if t.date() == day]
        for st in _slot_times(cfg):
            slot = dt.datetime.combine(day, st, tzinfo=TZ)
            if len(today) >= per_day:
                break
            if slot < earliest or any(abs(slot - t) < gap for t in today):
                continue
            out.append(slot)
            today.append(slot)
    return out


def next_slot(cfg, jobs, preferred=None, exclude=None):
    """The job's planned slot if it's still free and in the future, else the earliest free slot."""
    free = free_slots(cfg, jobs, 60, exclude=exclude)
    if preferred:
        p = dt.datetime.fromisoformat(preferred).astimezone(TZ)
        if p in free:
            return p
    return free[0] if free else None


def role_for(cfg, slot):
    """Portfolio role of a slot: the n-th slot of the day gets the n-th role (discovery, authority, retention).
    With fewer slots than roles (2 a day, 3 roles) the sequence continues across days, so no series is starved;
    with equal counts this is exactly slot n → role n."""
    roles = cfg.get("planning", {}).get("roles", ["discovery", "authority", "retention"])
    times = _slot_times(cfg)
    local = slot.astimezone(TZ)
    t = local.time()
    idx = min(range(len(times)), key=lambda i: abs(times[i].hour * 60 + times[i].minute - t.hour * 60 - t.minute))
    return roles[(local.date().toordinal() * len(times) + idx) % len(roles)]


def counts_for_day(jobs, day):
    c = {"planned_or_ready": 0, "awaiting_approval": 0, "scheduled": 0, "published": 0}
    for j in jobs:
        if not when(j) or when(j).date() != day:
            continue
        if j["status"] == "published":
            c["published"] += 1
        elif j["status"] == "scheduled":
            c["scheduled"] += 1
        elif j["status"] == "ready":
            c["planned_or_ready"] += 1
        elif j["status"] == "awaiting_approval":
            c["awaiting_approval"] += 1
    return c
