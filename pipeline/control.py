"""Run control: kill switch, dry-run, per-run limits and usage accounting."""
import csv
import datetime as dt

from .util import ROOT, DATA, log

STOP_FILE = ROOT / "STOP"          # create this file (touch STOP) to halt all new work and publishing
CHANNEL_STOP = None                # STOP.<channel id>: halts one channel only (set by channels.activate)
USAGE = DATA / "usage.csv"

DRY_RUN = False                    # set by main.py --dry-run: everything runs except uploads/comments/deletes
_counts = {"gemini_calls": 0, "gemini_vision_calls": 0, "tts_calls": 0, "renders": 0, "uploads": 0,
           "upload_units": 0, "videos_made": 0, "videos_quarantined": 0}


def killed(cfg):
    if (STOP_FILE.exists() or (CHANNEL_STOP and CHANNEL_STOP.exists())
            or cfg.get("control", {}).get("kill_switch")):
        log("KILL SWITCH is on (STOP / STOP.<channel> file or control.kill_switch): no new work or publishing")
        return True
    return False


def count(key, n=1):
    _counts[key] = _counts.get(key, 0) + n


def uploads_left(cfg):
    return cfg.get("control", {}).get("max_uploads_per_run", 5) - _counts["uploads"]


def gemini_budget_left(cfg):
    limit = cfg.get("control", {}).get("max_gemini_calls_per_run", 120)
    return limit - _counts["gemini_calls"] - _counts["gemini_vision_calls"]


def write_usage(command):
    """Appends this run's usage to data/usage.csv (the basis for cost per video)."""
    new = not USAGE.exists()
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE, "a", newline="") as f:
        w = csv.writer(f)
        keys = sorted(_counts)
        if new:
            w.writerow(["timestamp", "command", "dry_run", *keys])
        w.writerow([dt.datetime.now().isoformat(timespec="seconds"), command, DRY_RUN, *[_counts[k] for k in keys]])
