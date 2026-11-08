"""Debt accounts and payment reconciliation (Phase 7, phase C), on the owner-level store.

Accounts (debts.json) hold only what the owner entered or a lender statement says. A missing rate, balance or
due date stays None and every view says "unknown": nothing here is estimated into existence. Each account has a
statement status: "unverified" (owner's word), "verified" (a lender statement was entered, `statements[]`).

Payments are ledger events (money.py: personal debt_payment / business debt_repayment, account = the account id),
so there is one history of money and a payment can't be counted twice:
  - the idempotency key makes a re-run a no-op, and an external reference can't be reused for a second payment
    (money.validate_unique_ref);
  - two payments on one account with the same amount within 3 days are flagged "possible duplicate" for review;
  - a mistaken payment is never edited or deleted: a reversal event points at it, both stay in the history, and
    neither counts.
Instalment status (from the contractual EMI and next due date, payments allocated oldest-first):
  scheduled (later), due (within 7 days or today), paid, partially_paid, overdue (past due, not covered),
  reversed (its payment was reversed and nothing replaced it).
Statement reconciliation: every payment a statement lists is matched to a ledger payment (same amount, ±3 days)
and every ledger payment in the statement period must appear on it; anything unmatched, or a statement balance
that disagrees with the owner-entered one, is a discrepancy for manual review. Nothing is auto-corrected.

Strategies: minimum payments, avalanche (highest verified rate first) and snowball (smallest verified balance
first). The order is shown when the needed fields are known. The interest impact is simulated only when every
account in the plan has a verified balance, a known rate with "reducing" interest, and an EMI; otherwise it says
which data is missing. Extra repayment is never recommended while the personal forecast is below the reserve.
"""
import datetime as dt
import re

from . import store

DEBTS = "debts.json"
RATE_TYPES = ("reducing", "flat", "unknown")
FIELDS = ("lender", "loan_type", "account_ref", "principal_paise", "principal_as_of", "rate_annual_pct",
          "rate_type", "emi_paise", "due_day", "next_due", "overdue_paise", "fees_paise", "foreclosure_quote_paise",
          "foreclosure_valid_until", "foreclosure_charges_paise", "foreclosure_confirmed_by_lender", "in_aggregate",
          "kind", "note")


def mask(ref):
    """Only the last 4 characters of an account number are ever stored."""
    s = re.sub(r"\W", "", str(ref or ""))
    return ("XXXX" + s[-4:]) if s else None


def accounts():
    return store.load(DEBTS, {"accounts": []})["accounts"]


def get(account_id):
    return next((a for a in accounts() if a["id"] == account_id), None)


def _clean(fields):
    out = {}
    for k, v in fields.items():
        if v is None or k not in FIELDS:
            continue
        if k == "account_ref":
            v = mask(v)
        elif k.endswith("_paise"):
            v = store.paise(v) if not isinstance(v, int) else v
            if v < 0:
                raise ValueError(f"{k} can't be negative")
        elif k == "rate_annual_pct":
            v = float(v)
            if not 0 <= v <= 100:
                raise ValueError("rate_annual_pct must be 0–100")
        elif k == "rate_type" and v not in RATE_TYPES:
            raise ValueError(f"rate_type must be one of {RATE_TYPES}")
        elif k == "due_day":
            v = int(v)
            if not 1 <= v <= 31:
                raise ValueError("due_day must be 1–31")
        elif k in ("next_due", "principal_as_of", "foreclosure_valid_until"):
            v = dt.date.fromisoformat(v).isoformat()
        elif k in ("foreclosure_confirmed_by_lender", "in_aggregate"):
            v = bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes")
        out[k] = v
    return out


def upsert(account_id, **fields):
    """Creates or updates an account. Every change is kept in the account's `changes` (old → new), so a
    correction never erases what was entered before."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,30}", account_id or ""):
        raise ValueError("account id: 2–31 lowercase letters, digits, - or _ (e.g. kotak-pl, friend-ravi)")
    new = _clean(fields)
    with store.lock():
        data = store.load(DEBTS, {"accounts": []})
        acc = next((a for a in data["accounts"] if a["id"] == account_id), None)
        if acc is None:
            if not new.get("lender"):
                raise ValueError("a new account needs --lender")
            acc = {"id": account_id, "rate_type": "unknown", "statement_status": "unverified", "statements": [],
                   "changes": [], "created": dt.date.today().isoformat(), "kind": "loan", "in_aggregate": False}
            data["accounts"].append(acc)
        changed = {k: (acc.get(k), v) for k, v in new.items() if acc.get(k) != v}
        for k, (_old, v) in changed.items():
            acc[k] = v
        if changed:
            acc["changes"].append({"on": dt.datetime.now().isoformat(timespec="seconds"),
                                   "fields": {k: list(ov) for k, ov in changed.items()}})
        store.save(DEBTS, data)
    return acc, bool(changed)


# ---------------------------------------------------------------- payments and instalments

def payments(account_id, evs=None):
    """(effective payments, reversed payments, possible duplicates) for one account, from the ledger."""
    from . import money
    evs = evs if evs is not None else store.reconcile(money.validate)[0]
    reversed_ids = {e.get("reverses") for e in evs if e["type"] == "reversal"}
    mine = [e for e in evs if e["type"] in ("debt_payment", "debt_repayment") and e.get("account") == account_id]
    live = [e for e in mine if e["id"] not in reversed_ids]
    dups = []
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            gap = abs((dt.date.fromisoformat(a["date"]) - dt.date.fromisoformat(b["date"])).days)
            if a["amount_paise"] == b["amount_paise"] and gap <= 3:
                dups.append((a["id"], b["id"]))
    return sorted(live, key=lambda e: e["date"]), [e for e in mine if e["id"] in reversed_ids], dups


def _add_month(d, day):
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    import calendar
    return dt.date(y, m, min(day, calendar.monthrange(y, m)[1]))


def instalments(acc, until, today=None, evs=None):
    """Contractual instalments from the account's next due date up to `until`, each with a status. [] when the
    EMI or the due date isn't known (never invented)."""
    today = today or dt.date.today()
    if not acc.get("emi_paise") or not acc.get("next_due"):
        return []
    live, rev, _ = payments(acc["id"], evs)
    start = dt.date.fromisoformat(acc["next_due"])
    # a payment counts toward instalments from 5 days before the first one listed: an earlier payment belongs to an
    # earlier instalment (e.g. the October EMI already paid must not mark November paid)
    pool = sum(e["amount_paise"] for e in live if e["date"] >= (start - dt.timedelta(days=5)).isoformat())
    out, d, day = [], start, acc.get("due_day") or start.day
    while d <= until:
        amt = acc["emi_paise"]
        paid = min(pool, amt)
        pool -= paid
        if paid >= amt:
            st = "paid"
        elif paid > 0:
            st = "partially_paid"
        elif d < today:
            st = "reversed" if rev and any(e["date"] <= d.isoformat() for e in rev) else "overdue"
        elif (d - today).days <= 7:
            st = "due"
        else:
            st = "scheduled"
        out.append({"due": d.isoformat(), "amount_paise": amt, "paid_paise": paid, "status": st})
        d = _add_month(d, day)
    return out


# ---------------------------------------------------------------- lender statements

def add_statement(account_id, statement_date, closing_principal, payments_listed=(), interest=None, fees=None,
                  source=""):
    """A lender statement the owner copied in: closing balance and the payments it lists [(date, amount)]. Marks
    the account verified as of that date; the original entries and earlier statements are kept."""
    if not source:
        raise ValueError("a statement needs --ref naming its source (e.g. 'Kotak statement Oct 2026, PDF')")
    st = {"date": dt.date.fromisoformat(statement_date).isoformat(), "closing_principal_paise": store.paise(closing_principal),
          "payments": [{"date": dt.date.fromisoformat(d).isoformat(), "amount_paise": store.paise(a)} for d, a in payments_listed],
          "interest_paise": store.paise(interest) if interest is not None else None,
          "fees_paise": store.paise(fees) if fees is not None else None, "source": source,
          "entered": dt.datetime.now().isoformat(timespec="seconds")}
    with store.lock():
        data = store.load(DEBTS, {"accounts": []})
        acc = next((a for a in data["accounts"] if a["id"] == account_id), None)
        if not acc:
            raise ValueError(f"no debt account {account_id}")
        if any(s["date"] == st["date"] and s["source"] == source for s in acc["statements"]):
            return acc, False
        acc["statements"].append(st)
        acc["statement_status"] = "verified"
        store.save(DEBTS, data)
    return acc, True


def reconcile(account_id, evs=None):
    """Discrepancies between the ledger and the latest lender statement of one account, for manual review."""
    acc = get(account_id)
    if not acc:
        raise ValueError(f"no debt account {account_id}")
    live, _rev, dups = payments(account_id, evs)
    issues = [f"possible duplicate payments {a} and {b} (same amount within 3 days): check your bank statement"
              for a, b in dups]
    if not acc["statements"]:
        return issues + ["no lender statement entered: balance, rate and payments are unverified"]
    st = sorted(acc["statements"], key=lambda s: s["date"])[-1]
    prev = sorted(acc["statements"], key=lambda s: s["date"])[-2]["date"] if len(acc["statements"]) > 1 else None
    period = [e for e in live if e["date"] <= st["date"] and (prev is None or e["date"] > prev)]
    unmatched = list(period)
    for p in st["payments"]:
        hit = next((e for e in unmatched if e["amount_paise"] == p["amount_paise"]
                    and abs((dt.date.fromisoformat(e["date"]) - dt.date.fromisoformat(p["date"])).days) <= 3), None)
        if hit:
            unmatched.remove(hit)
        else:
            issues.append(f"statement lists a payment of {store.rupees(p['amount_paise'])} on {p['date']} that the "
                          "ledger doesn't have (record it with its reference, or query it with the lender)")
    issues += [f"ledger payment {e['id']} {store.rupees(e['amount_paise'])} on {e['date']} is not on the "
               f"{st['date']} statement" for e in unmatched]
    if acc.get("principal_paise") is not None and acc.get("principal_as_of") == st["date"] \
            and acc["principal_paise"] != st["closing_principal_paise"]:
        issues.append(f"entered balance {store.rupees(acc['principal_paise'])} differs from the statement's "
                      f"{store.rupees(st['closing_principal_paise'])} on {st['date']}")
    return issues


def balance(acc, evs=None):
    """(paise or None, basis): the latest statement's closing balance, else the owner-entered one, with the
    payments recorded after it listed (their principal/interest split is unknown, so they aren't subtracted)."""
    sts = sorted(acc.get("statements") or [], key=lambda s: s["date"])
    if sts:
        p, on, basis = sts[-1]["closing_principal_paise"], sts[-1]["date"], "lender statement"
    elif acc.get("principal_paise") is not None:
        p, on, basis = acc["principal_paise"], acc.get("principal_as_of"), "owner-entered, unverified"
    else:
        return None, "unknown"
    later = [e for e in payments(acc["id"], evs)[0] if on and e["date"] > on]
    note = f"{basis} as of {on or '?'}"
    if later:
        note += (f"; {len(later)} later payment(s) of {store.rupees(sum(e['amount_paise'] for e in later))} not yet "
                 "on a statement (principal share unknown)")
    return p, note


def total(evs=None):
    """Outstanding debt: the aggregate estimate stands in for every account marked in_aggregate (never added
    twice); separate accounts are added only when their balance is known."""
    accs = accounts()
    agg = [a for a in accs if a.get("kind") == "aggregate"]
    known, unknown, parts = 0, [], []
    for a in accs:
        if a.get("in_aggregate") and agg:
            continue
        p, _ = balance(a, evs)
        if p is None:
            unknown.append(a["id"])
        else:
            known += p
            parts.append(a["id"])
    return {"known_paise": known, "accounts": parts, "unknown": unknown,
            "covered_by_aggregate": [a["id"] for a in accs if a.get("in_aggregate") and agg]}


# ---------------------------------------------------------------- strategies

def _ready(acc):
    missing = [k for k, ok in (("verified balance", acc.get("statement_status") == "verified"),
                               ("rate", acc.get("rate_annual_pct") is not None),
                               ("reducing-balance interest", acc.get("rate_type") == "reducing"),
                               ("EMI", bool(acc.get("emi_paise")))) if not ok]
    return missing


def simulate(accs, extra_paise, order, months=240):
    """Monthly reducing-balance simulation: (months to clear all, total interest paise). EMI on every account,
    the extra (plus freed EMIs, once a loan clears) goes to the first unpaid account in `order`."""
    bal = {a["id"]: balance(a)[0] for a in accs}
    rate = {a["id"]: a["rate_annual_pct"] / 1200 for a in accs}
    emi = {a["id"]: a["emi_paise"] for a in accs}
    interest = 0
    for m in range(1, months + 1):
        free = extra_paise
        for i in bal:
            if bal[i] <= 0:
                free += emi[i]
                continue
            it = round(bal[i] * rate[i])
            interest += it
            bal[i] += it
            pay = min(bal[i], emi[i])
            bal[i] -= pay
            free += emi[i] - pay
        for i in order:
            if bal[i] > 0 and free > 0:
                pay = min(bal[i], free)
                bal[i] -= pay
                free -= pay
        if all(b <= 0 for b in bal.values()):
            return m, interest
    return None, interest


def strategies(extra_paise=0, accs=None):
    accs = accs if accs is not None else [a for a in accounts() if a.get("kind") != "aggregate"]
    out = {"minimum": [{"id": a["id"], "emi_paise": a.get("emi_paise"), "next_due": a.get("next_due")} for a in accs],
           "avalanche": None, "snowball": None, "impact": None, "missing": {}}
    with_rate = [a for a in accs if a.get("rate_annual_pct") is not None]
    with_bal = [(a, balance(a)[0]) for a in accs if balance(a)[0] is not None]
    if len(accs) >= 2 and len(with_rate) == len(accs):
        out["avalanche"] = [a["id"] for a in sorted(accs, key=lambda a: -a["rate_annual_pct"])]
    if len(accs) >= 2 and len(with_bal) == len(accs):
        out["snowball"] = [a["id"] for a, _p in sorted(with_bal, key=lambda x: x[1])]
    out["missing"] = {a["id"]: m for a in accs if (m := _ready(a))}
    if accs and not out["missing"] and out["avalanche"] and out["snowball"]:
        base = simulate(accs, 0, out["avalanche"])
        out["impact"] = {"minimum_only": base, "avalanche": simulate(accs, extra_paise, out["avalanche"]),
                         "snowball": simulate(accs, extra_paise, out["snowball"]), "extra_paise": extra_paise}
    return out


# ---------------------------------------------------------------- terminal view

def lines(evs=None, today=None):
    from . import money
    R = store.rupees
    today = today or dt.date.today()
    evs = evs if evs is not None else store.reconcile(money.validate)[0]
    t = total(evs)
    out = ["DEBT ACCOUNTS (owner-entered unless a lender statement verified it; unknown = not entered)"]
    for a in accounts():
        p, basis = balance(a, evs)
        u = lambda k, f=R: f(a[k]) if a.get(k) is not None else "unknown"
        out += [f"- {a['id']}: {a['lender']} · {a.get('loan_type') or 'type unknown'} · ref {a.get('account_ref') or '-'}"
                f" · {a['statement_status'].upper()}" + (" · inside the aggregate estimate" if a.get("in_aggregate") else ""),
                f"    outstanding {R(p) if p is not None else 'unknown'} ({basis})",
                f"    rate {str(a['rate_annual_pct']) + '% ' + a['rate_type'] if a.get('rate_annual_pct') is not None else 'unknown'}"
                f" · EMI {u('emi_paise')} · due day {a.get('due_day') or 'unknown'} · next due {a.get('next_due') or 'unknown'}"
                f" · overdue {u('overdue_paise')} · fees {u('fees_paise')}"]
        if a.get("foreclosure_quote_paise") is not None:
            out.append(f"    foreclosure quote {R(a['foreclosure_quote_paise'])} valid until "
                       f"{a.get('foreclosure_valid_until') or 'unknown'}, charges {u('foreclosure_charges_paise')}, "
                       f"{'CONFIRMED by the lender' if a.get('foreclosure_confirmed_by_lender') else 'NOT confirmed by the lender (optional, not a payment due)'}")
        for i in instalments(a, today + dt.timedelta(days=60), today, evs):
            out.append(f"    instalment {i['due']}: {R(i['amount_paise'])} {i['status']}"
                       + (f" (paid {R(i['paid_paise'])})" if i["status"] == "partially_paid" else ""))
        live, rev, _ = payments(a["id"], evs)
        out.append(f"    payments recorded: {len(live)} ({R(sum(e['amount_paise'] for e in live))})"
                   + (f", {len(rev)} reversed" if rev else ""))
        for issue in reconcile(a["id"], evs):
            out.append(f"    REVIEW: {issue}")
    out += [f"Outstanding debt (known): {R(t['known_paise'])} from {', '.join(t['accounts']) or 'none'}"
            + (f"; covered by the aggregate estimate: {', '.join(t['covered_by_aggregate'])}" if t["covered_by_aggregate"] else "")
            + (f"; balance unknown: {', '.join(t['unknown'])}" if t["unknown"] else "")]
    return out


def strategy_lines(extra_paise=0, personal_safe=False):
    R = store.rupees
    s = strategies(extra_paise if personal_safe else 0)
    out = ["REPAYMENT STRATEGY (information, not advice; nothing is paid automatically)",
           "Minimum contractual payments: " + (", ".join(f"{m['id']} {R(m['emi_paise']) if m['emi_paise'] else 'EMI unknown'}"
                                                         f" next {m['next_due'] or 'unknown'}" for m in s["minimum"]) or "none entered")]
    out.append("Avalanche (highest rate first): " + (" → ".join(s["avalanche"]) if s["avalanche"] else
                                                     "needs a known rate on at least 2 individual accounts"))
    out.append("Snowball (smallest balance first): " + (" → ".join(s["snowball"]) if s["snowball"] else
                                                        "needs a known balance on at least 2 individual accounts"))
    if not personal_safe:
        out.append("Additional repayment: NOT RECOMMENDED: the personal forecast is below the survival reserve")
    if s["impact"]:
        im = s["impact"]
        fmt = lambda r: f"{r[0]} months, interest {R(r[1])}" if r[0] else f"not cleared in 20 years, interest {R(r[1])}"
        out += [f"Estimated impact (verified data, reducing balance; extra {R(im['extra_paise'])}/month):",
                f"  minimum only: {fmt(im['minimum_only'])}", f"  avalanche: {fmt(im['avalanche'])}",
                f"  snowball: {fmt(im['snowball'])}"]
    else:
        out.append("Interest impact: not estimated: " + ("; ".join(f"{k} missing {', '.join(v)}" for k, v in
                                                                  s["missing"].items()) or "no individual accounts"))
    return out
