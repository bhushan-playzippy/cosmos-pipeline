import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ASSETS = ROOT / "assets"
OUTPUT = ROOT / "output"


def load_env():
    """Tiny .env loader (no extra dependency)."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config():
    import yaml
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class CorruptState(RuntimeError):
    """A state file exists but can't be parsed. Never treated as empty: an empty queue would free every slot."""


def read_json(path, default, strict=False):
    """strict: raise CorruptState on a damaged file instead of returning the default (queues, registries)."""
    path = Path(path)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            if strict:
                raise CorruptState(f"{path} is damaged ({e}); restore it from {path.name}.bak or git") from e
    return default


def write_json(path, obj):
    """Atomic: write a temp file, keep the previous version as .bak, then rename over the original.
    A crash mid-write leaves either the old or the new file, never half of one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, indent=2, ensure_ascii=False))
        f.flush()
        os.fsync(f.fileno())
    if path.exists():
        os.replace(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)


def slugify(text, max_len=50):
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len] or "video"


def run(cmd, cwd=None, timeout=1800):
    """Run a command, raise with readable stderr on failure. Bounded: a wedged ffmpeg must not hang the run."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(map(str, cmd))[:300]}")
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(map(str, cmd))}\n{p.stderr[-2000:]}")
    return p.stdout


def media_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


# Anything that looks like a credential is masked before it reaches a log line or the event log.
_SECRET = re.compile(r"(\"?(?:refresh_token|access_token|client_secret|token)\"?\s*[:=]\s*\"?)?"
                     r"(ya29\.[\w.-]+|1//[\w.-]{20,}|AIza[\w-]{30,}|EAA\w{30,}|GOCSPX-[\w-]+|gsk_\w{20,}|sk-or-v1-\w{20,}|(?(1)[^\s\",}]+|(?!x)x))")


def redact(text):
    return _SECRET.sub(lambda m: (m.group(1) or "") + "[redacted]", str(text))


LOG_TAG = "pipeline"   # channels.activate() sets this to the channel id so every line says which channel it's about
EVENTS = None          # channels.activate() points this at the channel's events.jsonl (structured log)


def log(msg):
    print(f"[{LOG_TAG}] {redact(msg)}", flush=True)


def event(kind, **fields):
    """Structured, append-only event log (one JSON object per line), redacted like log()."""
    if not EVENTS:
        return
    Path(EVENTS).parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "channel": LOG_TAG, "event": kind,
           **{k: (redact(v) if isinstance(v, str) else v) for k, v in fields.items()}}
    with open(EVENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
