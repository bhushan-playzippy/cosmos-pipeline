"""Channel profiles: one process works on exactly one channel.

  config.yaml             the original channel (id "cosmic"). Keeps its original file locations, so nothing about
                          the running channel moves: data/, output/, reports/, youtube_token.json, YOUTUBE_TOKEN_JSON.
  channels/<id>.yaml      every other channel. A complete, standalone profile (nothing is inherited from
                          config.yaml, so a missing key can never silently pick up another channel's setting).

activate() points every module's state at the chosen channel: queue, outputs, reports, analytics, usage, ledger,
OAuth token, kill switch, timezone and log tag. Isolation is enforced, not assumed:
  - two profiles may not share a YouTube channel id, a token file or a token environment variable (validate_all)
  - every upload first checks that the token's channel is the profile's youtube_channel_id (publish_youtube)
  - a per-channel lock file stops two runs of the same channel from racing on its queue (lock)
"""
import contextlib
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from .util import DATA, OUTPUT, ROOT, load_config

PROFILES = ROOT / "channels"
ACTIVE = None  # id of the activated channel


class ProfileError(SystemExit):
    pass


def default_id():
    return load_config()["channel"].get("id", "cosmic")


def available():
    return [default_id()] + sorted(p.stem for p in PROFILES.glob("*.yaml"))


def _read(name):
    import yaml
    if name == default_id():
        return load_config()
    path = PROFILES / f"{name}.yaml"
    if not path.exists():
        raise ProfileError(f"no channel profile '{name}' (have: {', '.join(available())})")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def paths(cfg):
    """Where a channel's state and credentials live. The original channel keeps the pre-multi-channel layout."""
    cid = cfg["channel"]["id"]
    cred = cfg.get("credentials") or {}
    if cid == default_id():
        p = {"data": DATA, "output": OUTPUT, "reports": ROOT / "reports",
             "token": ROOT / "youtube_token.json", "token_env": "YOUTUBE_TOKEN_JSON",
             "analytics_token": ROOT / "youtube_analytics_token.json",
             "ig_user_env": "IG_USER_ID", "ig_token_env": "IG_ACCESS_TOKEN"}
    else:
        secrets = ROOT / "credentials" / cid
        p = {"data": DATA / "channels" / cid, "output": OUTPUT / "channels" / cid, "reports": ROOT / "reports" / cid,
             "token": secrets / "youtube_token.json", "token_env": f"YOUTUBE_TOKEN_JSON_{cid.upper()}",
             "analytics_token": secrets / "youtube_analytics_token.json",
             "ig_user_env": f"IG_USER_ID_{cid.upper()}", "ig_token_env": f"IG_ACCESS_TOKEN_{cid.upper()}"}
    p["client_secret"] = ROOT / cred["client_secret"] if cred.get("client_secret") else ROOT / "client_secret.json"
    if cred.get("token_file"):
        p["token"] = ROOT / cred["token_file"]
    if cred.get("token_env"):
        p["token_env"] = cred["token_env"]
    p["stop"] = ROOT / f"STOP.{cid}"  # per-channel kill switch (the global STOP file still stops everything)
    p["events"] = p["data"] / "events.jsonl"
    return p


def validate(cfg, name):
    problems = []
    ch = cfg.get("channel", {})
    if ch.get("id") != name:
        problems.append(f"channel.id is {ch.get('id')!r}, expected {name!r} (the profile's file name)")
    sch = cfg.get("schedule", {})
    if not isinstance(sch.get("videos_per_day"), int) or sch["videos_per_day"] < 1:
        problems.append("schedule.videos_per_day must be a positive integer")
    if not (sch.get("slots") or sch.get("slots_ist")):
        problems.append("schedule.slots is missing")
    try:
        ZoneInfo(sch.get("timezone", "Asia/Kolkata"))
    except Exception:
        problems.append(f"schedule.timezone {sch.get('timezone')!r} is not a valid IANA timezone")
    if cfg.get("youtube", {}).get("enabled") and not ch.get("youtube_channel_id"):
        problems.append("youtube.enabled is true but channel.youtube_channel_id is empty (uploads would be unverified)")
    if problems:
        raise ProfileError(f"channel profile '{name}' is invalid:\n  - " + "\n  - ".join(problems))


def validate_all():
    """No two channels may share an identity or a credential: that is how cross-channel uploads happen."""
    seen = {}
    for name in available():
        cfg = _read(name)
        validate(cfg, name)
        p = paths(cfg)
        for key, val in (("youtube_channel_id", cfg["channel"].get("youtube_channel_id")),
                         ("token file", str(p["token"])), ("token env", p["token_env"]),
                         ("Instagram token env", p["ig_token_env"]),
                         ("data dir", str(p["data"]))):
            if not val:
                continue
            if (key, val) in seen:
                raise ProfileError(f"channels '{seen[(key, val)]}' and '{name}' share the same {key} ({val})")
            seen[(key, val)] = name


def load(name=None):
    name = name or os.environ.get("PIPELINE_CHANNEL") or default_id()
    cfg = _read(name)
    validate(cfg, name)
    cfg["_paths"] = paths(cfg)
    return cfg


def activate(cfg):
    """Points every module at this channel's files. Call once per process, before any work."""
    global ACTIVE
    from . import (analytics, control, finance, ideas, jobs, orchestrator, publish_instagram, publish_youtube, qa,
                   util, visuals)
    p = cfg["_paths"]
    jobs.HISTORY = p["data"] / "history.json"
    jobs.OUTPUT = orchestrator.OUTPUT = qa.OUTPUT = p["output"]
    jobs.TZ = ZoneInfo(cfg["schedule"].get("timezone", "Asia/Kolkata"))
    analytics.ADIR = p["data"] / "analytics"
    analytics.REPORTS = p["reports"]
    analytics.ANALYTICS_TOKEN = p["analytics_token"]
    control.USAGE = p["data"] / "usage.csv"
    control.CHANNEL_STOP = p["stop"]
    finance.LEDGER = p["data"] / "finance" / "ledger.csv"
    ideas.BACKLOG, ideas.HISTORY = p["data"] / "topics.json", p["data"] / "history.json"
    visuals.USED, visuals.VISION_CACHE = p["data"] / "used_media.json", p["data"] / "vision_cache.json"
    publish_youtube.TOKEN, publish_youtube.TOKEN_ENV = p["token"], p["token_env"]
    publish_youtube.CLIENT_SECRET = p["client_secret"]
    publish_instagram.USER_ENV, publish_instagram.TOKEN_ENV = p["ig_user_env"], p["ig_token_env"]
    publish_youtube.EXPECTED_CHANNEL = cfg["channel"].get("youtube_channel_id") or None
    util.LOG_TAG = cfg["channel"]["id"]
    util.EVENTS = p["events"]
    ACTIVE = cfg["channel"]["id"]
    p["data"].mkdir(parents=True, exist_ok=True)
    return cfg


@contextlib.contextmanager
def lock(cfg):
    """Exclusive per-channel lock: a second run of the same channel exits instead of racing on the queue.
    Different channels have different lock files, so they can run at the same time."""
    import fcntl
    path = cfg["_paths"]["data"] / ".pipeline.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"another run of channel '{cfg['channel']['id']}' is in progress ({path}); not starting")
        f.write(str(os.getpid()))
        f.flush()
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
