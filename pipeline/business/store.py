"""Owner-level store (Phase 7, phase A): one event ledger for every money fact, plus small JSON documents.

Rules this module enforces, because the old per-channel CSV ledger (pipeline/finance.py) couldn't:
  - Money is integer paise with an explicit currency. Amounts are parsed from text ("1,234.50"), never floats,
    and different currencies are never added together.
  - Every event has a stable id and an idempotency key. Recording the same key again with the same content is a
    no-op; the same key with different content is refused (a re-run never double-counts, a typo never
    overwrites history).
  - Writes are atomic with a .bak (util.write_json) under an exclusive lock, and the ledger is read strictly:
    a damaged or missing-but-backed-up file raises CorruptState instead of reading as "no money".
  - Malformed events are reported by reconcile(), never silently dropped or counted.
  - Owner-level: BASE does not move with channels.activate, and it is git-ignored (data/business/).
"""
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

from ..util import DATA, CorruptState, read_json, write_json

BASE = DATA / "business"
CURRENCIES = {"INR", "USD", "EUR", "GBP"}


def path(name):
    return Path(BASE) / name


@contextlib.contextmanager
def lock():
    """Exclusive owner-level lock (separate from the per-channel pipeline locks)."""
    import fcntl
    p = path(".business.lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another business/finance command is running; try again in a moment")
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def paise(text):
    """'1,234.50' / '₹1,22,000' / 1500 → integer paise. Refuses floats with more than 2 decimals and junk."""
    if isinstance(text, bool):
        raise ValueError("not an amount")
    if isinstance(text, int):
        return text * 100
    s = re.sub(r"[₹,\s]|INR|Rs\.?", "", str(text), flags=re.I)
    m = re.fullmatch(r"(-?)(\d+)(?:\.(\d{1,2}))?", s)
    if not m:
        raise ValueError(f"not an amount: {text!r}")
    p = int(m.group(2)) * 100 + int((m.group(3) or "0").ljust(2, "0"))
    return -p if m.group(1) else p


def rupees(p, currency="INR"):
    """Paise → '₹1,22,000.00' (Indian grouping) for INR, '12,000.00 USD' otherwise."""
    sign, p = ("-" if p < 0 else ""), abs(int(p))
    whole, frac = divmod(p, 100)
    if currency == "INR":
        s = str(whole)
        head, tail = s[:-3], s[-3:]
        head = ",".join(re.findall(r"\d{1,2}(?=(?:\d{2})*$)", head)) if head else ""
        return f"{sign}₹{head + ',' if head else ''}{tail}.{frac:02d}"
    return f"{sign}{whole:,}.{frac:02d} {currency}"


# ---------------------------------------------------------------- documents (settings, plans, records)

def load(name, default):
    p = path(name)
    if not p.exists() and p.with_name(p.name + ".bak").exists():
        raise CorruptState(f"{p} is missing but {p.name}.bak exists (interrupted write?): restore it before continuing")
    return read_json(p, default, strict=True)


def save(name, obj):
    write_json(path(name), obj)


# ---------------------------------------------------------------- the event ledger

LEDGER = "ledger.json"


def events():
    data = load(LEDGER, {"version": 1, "events": []})
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise CorruptState(f"{path(LEDGER)} has an unexpected shape; restore it from the .bak")
    return data["events"]


def _fingerprint(ev):
    core = {k: ev.get(k) for k in sorted(ev) if k not in ("id", "recorded")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def record(ev, validate=None, conflict=None):
    """Appends one event (a dict with at least key, book, type, date, amount_paise, currency). Returns
    (event, created). Same key + same content → (existing, False); same key + different content → ValueError."""
    ev = dict(ev)
    if not ev.get("key"):
        raise ValueError("every event needs an idempotency key")
    if ev.get("currency") not in CURRENCIES:
        raise ValueError(f"unknown currency {ev.get('currency')!r}")
    if not isinstance(ev.get("amount_paise"), int) or isinstance(ev.get("amount_paise"), bool):
        raise ValueError("amount_paise must be an integer")
    dt.date.fromisoformat(str(ev.get("date")))
    if validate:
        validate(ev)
    with lock():
        evs = events()
        same = next((e for e in evs if e.get("key") == ev["key"]), None)
        if conflict and not same:
            conflict(ev, evs)  # checks that need the whole ledger (a reused payment reference), under the lock
        if same:
            if _fingerprint(same) == _fingerprint(ev):
                return same, False
            raise ValueError(f"idempotency key {ev['key']!r} already used for a different entry ({same['id']}); "
                             "use a new key or correct it with a reversal")
        ev["id"] = f"ev-{len(evs) + 1:05d}-{_fingerprint(ev)[:6]}"
        ev["recorded"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        evs.append(ev)
        save(LEDGER, {"version": 1, "events": evs})
    return ev, True


def reconcile(validate=None):
    """(valid events, problems): every event re-checked (shape, currency, integer paise, date, unique id/key,
    and `validate` for type rules). Totals are always computed from the valid list; problems are shown, never
    hidden or counted."""
    good, problems, ids, keys = [], [], set(), set()
    for i, e in enumerate(events()):
        try:
            if not isinstance(e, dict):
                raise ValueError("not an object")
            if e.get("id") in ids or e.get("key") in keys:
                raise ValueError("duplicate id or key")
            if e.get("currency") not in CURRENCIES or not isinstance(e.get("amount_paise"), int):
                raise ValueError("bad currency or amount")
            dt.date.fromisoformat(str(e.get("date")))
            if validate:
                validate(e)
        except (ValueError, TypeError) as err:
            problems.append({"index": i, "id": (e or {}).get("id") if isinstance(e, dict) else None,
                             "problem": str(err)})
            continue
        ids.add(e["id"])
        keys.add(e["key"])
        good.append(e)
    return good, problems


def key_for(*parts):
    """A deterministic idempotency key from the facts of an entry (re-running the same command is a no-op)."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]
