"""Personal and business finance (Phase 7, phase B), on the owner-level event ledger (store.py).

Two books that never mix:
  personal  salary and other income, essential and discretionary spending, personal debt payments, money
            borrowed from friends/family and paid back, and cash-balance snapshots
  business  cleared client receipts, refunds, direct production costs, paid operating expenses, tax paid,
            owner-confirmed withdrawals to the personal book, owner-confirmed debt repayments
Salary is never business revenue. Nothing pending (an invoice, an expected commission, views, a forecast) is an
event at all: forecasts live in plan.json and are shown apart from actuals.

Every actual event is a money fact the owner confirms (`confirmed: true`) with an external reference (bank or
UPI reference, receipt number, or "cash"); nothing here moves money or contacts anyone.

Business net available cash (owner decisions D1, D3, D4, 2 Nov):
  cleared receipts − refunds − direct costs − paid operating expenses − owner withdrawals − debt repayments
  − tax reserve still owed (30% of cleared profit, an estimate, not tax advice, minus tax actually paid)
  − operating reserve (₹5,000)
Extra debt repayment is suggested only from a positive amount, split by `allocation` (50% debt / 50% buffer),
and never while the personal 30-day forecast falls below the personal reserve.

Personal 30-day forecast: opening cash (the latest owner-confirmed balance plus personal events after it) and
every planned item in the window, in a conservative scenario (inflows smallest and latest, outflows largest and
earliest) and a best case (the reverse). Items with an unknown date must be given a date range; assumptions
are labelled.
"""
import datetime as dt
import math

from . import store

PERSONAL = {
    "balance": None,  # owner-confirmed cash on hand at a date (opening point of the running balance)
    "income": {"salary", "owner_withdrawal", "refund", "gift", "other"},
    "expense": {"rent", "food", "transport", "utilities", "essential_other", "discretionary"},
    "debt_payment": None,       # account required (lender or person)
    "borrowing_received": None,  # account required (friend/family)
    "reversal": None,            # undoes a mistaken entry (`reverses` = its id); both stay in the history
}
BUSINESS = {
    "receipt": {"client", "affiliate", "sponsorship", "other"},
    "refund": None,
    "direct_cost": None,
    "operating_expense": None,
    "tax_payment": None,
    "owner_withdrawal": None,
    "debt_repayment": None,     # account required
    "reversal": None,
}
INFLOW = {("personal", "income"), ("personal", "borrowing_received"), ("business", "receipt")}
DEFAULTS = {"currency": "INR", "tax_rate": 0.30, "business_operating_reserve_paise": 500_000,
            "personal_reserve_paise": 500_000, "allocation": {"extra_debt": 0.5, "buffer": 0.5},
            "salary_delay_days": 7}
ACCOUNT_TYPES = ("debt_payment", "borrowing_received", "debt_repayment")
PAYMENT_TYPES = ("debt_payment", "debt_repayment", "owner_withdrawal", "receipt", "refund", "direct_cost",
                 "operating_expense", "tax_payment", "income", "expense")


def settings():
    s = dict(DEFAULTS)
    s.update(store.load("settings.json", {}))
    return s


def validate(e):
    types = {"personal": PERSONAL, "business": BUSINESS}.get(e.get("book"))
    if types is None:
        raise ValueError(f"book must be personal or business, not {e.get('book')!r}")
    if e.get("type") not in types:
        raise ValueError(f"{e.get('book')} book has no type {e.get('type')!r} (one of {', '.join(types)})")
    cats = types[e["type"]]
    if cats and e.get("category") not in cats:
        raise ValueError(f"{e['type']} needs a category: {', '.join(sorted(cats))}")
    if e["type"] in ACCOUNT_TYPES:
        from . import debt
        if not e.get("account"):
            raise ValueError(f"{e['type']} needs --account (a debt account id: `money debts`)")
        if not debt.get(e["account"]):
            raise ValueError(f"no debt account {e['account']!r}: create it first (`money debt-add`)")
    if e["type"] == "reversal" and not e.get("reverses"):
        raise ValueError("a reversal needs the id of the entry it reverses")
    if e["amount_paise"] <= 0 and e["type"] != "balance":
        raise ValueError("amounts are positive; the type says which way the money went")
    if e["amount_paise"] < 0:
        raise ValueError("a balance can't be negative here; record what you owe as debt")
    if e.get("confirmed") is not True or not str(e.get("ref") or "").strip():
        raise ValueError("an actual money entry needs owner confirmation (--confirm) and an external reference "
                         "(--ref: bank/UPI reference, receipt number, or 'cash')")


def add(book, type_, amount, ref, confirmed, category=None, account=None, date=None, note="", currency="INR",
        key=None):
    """Records one actual entry. Returns (event, created). Re-running the same command is a no-op."""
    date = date or dt.date.today().isoformat()
    p = store.paise(amount)
    key = key or store.key_for(book, type_, category, account, date, p, currency, ref)
    return store.record({"key": key, "book": book, "type": type_, "category": category, "account": account,
                         "date": date, "amount_paise": p, "currency": currency, "ref": ref,
                         "confirmed": confirmed, "note": note}, validate, _ref_reused)


def _ref_reused(ev, evs):
    """One external reference = one money movement: the same bank/UPI reference on a second, different entry is a
    double count (a payment entered twice with a typo in the amount). "cash" is exempt; a withdrawal's two halves
    share their reference by design."""
    ref = str(ev.get("ref") or "").strip().lower()
    if ev["type"] not in PAYMENT_TYPES or ref in ("", "cash") or ev.get("category") == "owner_withdrawal":
        return
    for e in evs:
        if (e.get("type") in PAYMENT_TYPES and str(e.get("ref") or "").strip().lower() == ref
                and e.get("category") != "owner_withdrawal" and e.get("type") != "owner_withdrawal"):
            raise ValueError(f"reference {ev['ref']!r} is already used by {e['id']} ({e['type']} "
                             f"{store.rupees(e['amount_paise'], e['currency'])} on {e['date']}): a payment can't be "
                             "recorded twice. If that entry was wrong, reverse it (`money reverse`) and record again")


def reverse(event_id, ref, confirmed, date=None, note=""):
    """Undoes a mistaken entry without editing history: a reversal event of the same amount points at it, and
    neither counts in any total."""
    evs = store.reconcile(validate)[0]
    orig = next((e for e in evs if e["id"] == event_id), None)
    if not orig or orig["type"] == "reversal":
        raise ValueError(f"no entry {event_id} to reverse")
    if any(e.get("reverses") == event_id for e in evs):
        raise ValueError(f"{event_id} is already reversed")
    return store.record({"key": store.key_for("reversal", event_id), "book": orig["book"], "type": "reversal",
                         "reverses": event_id, "account": orig.get("account"), "date": date or dt.date.today().isoformat(),
                         "amount_paise": orig["amount_paise"], "currency": orig["currency"], "ref": ref,
                         "confirmed": confirmed, "note": note}, validate)


def effective(evs):
    """Events that count: reversals and the entries they reverse are kept in the ledger but drop out of totals."""
    gone = {e.get("reverses") for e in evs if e["type"] == "reversal"}
    return [e for e in evs if e["type"] != "reversal" and e["id"] not in gone]


def withdraw(amount, ref, confirmed, date=None, note=""):
    """Owner withdrawal: business cash → personal cash, recorded on both books with the same reference."""
    date = date or dt.date.today().isoformat()
    a = add("business", "owner_withdrawal", amount, ref, confirmed, date=date, note=note)
    b = add("personal", "income", amount, ref, confirmed, category="owner_withdrawal", date=date, note=note)
    return a, b


# ---------------------------------------------------------------- totals (always from the reconciled ledger)

def _signed(e):
    return e["amount_paise"] if (e["book"], e["type"]) in INFLOW else -e["amount_paise"]


def personal_cash(on=None, evs=None):
    """(paise or None, as_of, notes): latest confirmed balance at or before `on` plus later personal events.
    None when no balance was ever recorded: an opening balance is required, never assumed."""
    on = on or dt.date.today().isoformat()
    evs = effective(evs if evs is not None else store.reconcile(validate)[0])
    mine = [e for e in evs if e["book"] == "personal" and e["currency"] == "INR" and e["date"] <= on]
    snaps = sorted((e for e in mine if e["type"] == "balance"), key=lambda e: (e["date"], e["recorded"]))
    if not snaps:
        return None, None, ["no opening balance recorded: `main.py money balance --amount … --ref … --confirm`"]
    snap = snaps[-1]
    after = [e for e in mine if e["type"] != "balance" and (e["date"], e["recorded"]) > (snap["date"], snap["recorded"])]
    return snap["amount_paise"] + sum(_signed(e) for e in after), snap["date"], []


def business(evs=None):
    """Business book totals and the net-available-cash calculation (paise, INR only; other currencies listed)."""
    s = settings()
    evs = effective(evs if evs is not None else store.reconcile(validate)[0])
    b = [e for e in evs if e["book"] == "business"]
    inr = [e for e in b if e["currency"] == "INR"]
    t = lambda typ: sum(e["amount_paise"] for e in inr if e["type"] == typ)
    receipts, refunds, direct, opex = t("receipt"), t("refund"), t("direct_cost"), t("operating_expense")
    tax_paid, withdrawals, repaid = t("tax_payment"), t("owner_withdrawal"), t("debt_repayment")
    profit = receipts - refunds - direct - opex
    tax_due = max(0, math.ceil(max(0, profit) * s["tax_rate"]) - tax_paid)
    cash = receipts - refunds - direct - opex - tax_paid - withdrawals - repaid
    reserve = s["business_operating_reserve_paise"]
    available = cash - tax_due - reserve
    return {"cleared_receipts": receipts, "refunds": refunds, "direct_costs": direct, "operating_expenses": opex,
            "profit": profit, "tax_rate": s["tax_rate"], "tax_reserve": tax_due, "tax_paid": tax_paid,
            "owner_withdrawals": withdrawals, "debt_repayments": repaid, "business_cash": cash,
            "operating_reserve": reserve, "net_available": available,
            "by_source": {c: sum(e["amount_paise"] for e in inr if e["type"] == "receipt" and e["category"] == c)
                          for c in BUSINESS["receipt"]},
            "other_currency": [e for e in b if e["currency"] != "INR"]}


def recommendation(biz=None, fc=None):
    """What to do with business net available cash. Never a transfer: a suggestion the owner may act on."""
    s = settings()
    biz = biz or business()
    fc = fc if fc is not None else forecast()
    avail = biz["net_available"]
    if fc.get("error"):
        return {"action": "hold", "why": "the personal forecast can't be computed: " + fc["error"], "extra_debt": 0}
    low = min(fc[sc]["lowest"] for sc in SCENARIOS)
    if low < fc["reserve"]:
        return {"action": "hold", "extra_debt": 0, "why": (
            f"the personal forecast falls to {store.rupees(low)} (survival reserve {store.rupees(fc['reserve'])}): "
            "no extra debt repayment or discretionary business spending is recommended"),
            "withdrawable": max(0, avail),
            "note": "business cash above its reserves could be withdrawn to cover essentials, only by your decision"}
    if avail <= 0:
        return {"action": "hold", "extra_debt": 0, "why": "no business cash above the tax and operating reserves"}
    extra = avail * s["allocation"]["extra_debt"] // 1
    return {"action": "suggest", "extra_debt": int(extra), "buffer": int(avail - extra),
            "why": f"{s['allocation']['extra_debt']:.0%} of net available cash to extra debt, the rest kept as buffer"}


# ---------------------------------------------------------------- planned items and the 30-day forecast

PLAN = "plan.json"
SCENARIOS = ("conservative", "best", "delayed_salary")


def plan_items():
    return store.load(PLAN, {"items": []})["items"]


def plan(label, direction, amount_min, earliest, amount_max=None, latest=None, category="other", spread=False,
         assumption="", needs_action=False, key=None, optional=False, confirmed=True, account=None):
    """A forecast item (never cash): an expected inflow or outflow over a date range. optional: shown as a choice
    ("if you do this…") and kept out of the scenarios (a foreclosure quote isn't a payment due). confirmed=False:
    the amount or date is not confirmed (shown as UNCONFIRMED)."""
    if direction not in ("inflow", "outflow"):
        raise ValueError("direction must be inflow or outflow")
    lo, hi = store.paise(amount_min), store.paise(amount_max if amount_max is not None else amount_min)
    a, b = dt.date.fromisoformat(earliest), dt.date.fromisoformat(latest or earliest)
    if lo <= 0 or hi < lo or b < a:
        raise ValueError("need 0 < min ≤ max and earliest ≤ latest")
    item = {"id": key or store.key_for("plan", label, direction, earliest)[:10], "label": label, "direction": direction,
            "category": category, "min_paise": lo, "max_paise": hi, "earliest": a.isoformat(), "latest": b.isoformat(),
            "spread": bool(spread), "assumption": assumption, "needs_action": bool(needs_action),
            "optional": bool(optional), "confirmed": bool(confirmed), "account": account, "status": "planned"}
    with store.lock():
        data = store.load(PLAN, {"items": []})
        old = next((i for i in data["items"] if i["id"] == item["id"]), None)
        same = lambda x: {k: v for k, v in x.items() if k not in ("status", "changes")}
        if old and same(old) == same(item):
            return old, False
        if old:
            raise ValueError(f"plan item {item['id']} exists with different values: `money plan-set --note {item['id']} …`")
        data["items"].append(item)
        store.save(PLAN, data)
    return item, True


EDITABLE = ("label", "min_paise", "max_paise", "earliest", "latest", "spread", "assumption", "needs_action",
            "optional", "confirmed", "account", "category", "status")


def plan_set(item_id, reason, **fields):
    """Corrects a forecast item; the old values are kept in its `changes` with the reason."""
    if not reason:
        raise ValueError("say why the item changes (--assumption or --reason text)")
    with store.lock():
        data = store.load(PLAN, {"items": []})
        item = next((i for i in data["items"] if i["id"] == item_id), None)
        if not item:
            raise ValueError(f"no plan item {item_id}")
        new = {k: v for k, v in fields.items() if k in EDITABLE and v is not None}
        if "status" in new and new["status"] not in ("planned", "done", "cancelled"):
            raise ValueError("status must be planned, done or cancelled")
        changed = {k: [item.get(k), v] for k, v in new.items() if item.get(k) != v}
        if changed:
            item.update(new)
            if item["max_paise"] < item["min_paise"] or item["latest"] < item["earliest"]:
                raise ValueError("need min ≤ max and earliest ≤ latest")
            item.setdefault("changes", []).append({"on": dt.datetime.now().isoformat(timespec="seconds"),
                                                   "why": reason, "fields": changed})
            store.save(PLAN, data)
    return item


def plan_status(item_id, status):
    return plan_set(item_id, f"marked {status}", status=status)


def _flows(item, scenario, start, end, delay=0):
    """[(date, signed paise)] of one item in one scenario, clipped to the window (overdue items land on day 1).
    conservative: inflows smallest and latest, outflows largest and earliest; best: the reverse; delayed_salary:
    conservative with every salary item a further `delay` days late."""
    infl = item["direction"] == "inflow"
    worst = scenario != "best"
    amount = item["min_paise"] if infl == worst else item["max_paise"]
    a, b = dt.date.fromisoformat(item["earliest"]), dt.date.fromisoformat(item["latest"])
    if scenario == "delayed_salary" and item.get("category") == "salary":
        a, b = a + dt.timedelta(delay), b + dt.timedelta(delay)
    sign = 1 if infl else -1
    if item["spread"]:
        days = [a + dt.timedelta(d) for d in range((b - a).days + 1)]
        share, rest = divmod(amount, len(days))
        out = [(max(d, start), sign * (share + (1 if k < rest else 0))) for k, d in enumerate(days)]
    else:
        when = (b if infl else a) if worst else (a if infl else b)
        out = [(max(when, start), sign * amount)]
    return [(d, v) for d, v in out if d <= end]


def _debt_items(today, end):
    """Contractual instalments of debt accounts whose EMI and next due date are known and not yet paid; accounts
    without them are listed as gaps, never guessed into the forecast."""
    from . import debt
    items, gaps = [], []
    for acc in debt.accounts():
        if not acc.get("emi_paise") or not acc.get("next_due"):
            if acc.get("emi_paise") or acc.get("kind") == "aggregate":
                gaps.append(acc["id"])
            continue
        for i in debt.instalments(acc, end, today):
            if i["status"] in ("paid", "reversed"):
                continue
            left = i["amount_paise"] - i["paid_paise"]
            items.append({"id": f"emi-{acc['id']}-{i['due']}", "label": f"EMI {acc['lender']} ({i['status']})",
                          "direction": "outflow", "category": "debt", "min_paise": left, "max_paise": left,
                          "earliest": i["due"], "latest": i["due"], "spread": False, "assumption": "",
                          "needs_action": i["status"] in ("due", "overdue"), "optional": False,
                          "confirmed": acc.get("statement_status") == "verified", "account": acc["id"],
                          "status": "planned"})
    return items, gaps


def _run(cash, items, scenario, today, end, reserve, delay):
    flows = sorted((f for i in items for f in _flows(i, scenario, today, end, delay)), key=lambda f: f[0])
    bal, low, low_day, first_below = cash, cash, today, None
    for d, v in flows:
        bal += v
        if bal < low:
            low, low_day = bal, d
        if first_below is None and bal < reserve:
            first_below = d
    return {"closing": bal, "lowest": low, "lowest_on": low_day.isoformat(),
            "first_below_reserve": first_below.isoformat() if first_below else None,
            "shortfall_to_reserve": max(0, reserve - low),
            "inflows": sum(v for _d, v in flows if v > 0), "outflows": -sum(v for _d, v in flows if v < 0)}


def forecast(today=None, days=30):
    """Personal cash, day by day, in three scenarios. Never mixes in business money (a withdrawal is an actual
    personal income event once the owner confirms it). Optional items (e.g. an unconfirmed foreclosure quote) are
    shown as their own "what if", never inside the scenarios."""
    today = today or dt.date.today()
    end = today + dt.timedelta(days=days)
    s = settings()
    cash, as_of, notes = personal_cash(today.isoformat())
    if cash is None:
        return {"error": notes[0], "reserve": s["personal_reserve_paise"]}
    planned = [i for i in plan_items() if i["status"] == "planned"]
    emis, gaps = _debt_items(today, end)
    items = [i for i in planned if not i.get("optional")] + emis
    optional = [i for i in planned if i.get("optional")]
    reserve, delay = s["personal_reserve_paise"], s["salary_delay_days"]
    out = {"today": today.isoformat(), "end": end.isoformat(), "opening": cash, "opening_as_of": as_of,
           "reserve": reserve, "items": items, "optional": optional, "debt_gaps": gaps, "salary_delay_days": delay,
           "living": [i for i in items if i.get("category") == "essential_other"],
           "after_window": [i for i in items if dt.date.fromisoformat(i["earliest"]) > end],
           "needs_action": [i for i in items + optional if i["needs_action"]],
           "unconfirmed": [i for i in items + optional if not i.get("confirmed", True)],
           "assumptions": [i for i in items + optional if i["assumption"]]}
    for sc in SCENARIOS:
        out[sc] = _run(cash, items, sc, today, end, reserve, delay)
    out["with_optional"] = {i["id"]: _run(cash, items + [dict(i, optional=False)], "conservative", today, end,
                                          reserve, delay) for i in optional}
    out["warning"] = min(out[sc]["lowest"] for sc in SCENARIOS) < reserve
    return out


# ---------------------------------------------------------------- terminal views (never written to reports/)

def forecast_lines(fc=None):
    fc = fc or forecast()
    R = store.rupees
    if fc.get("error"):
        return [f"PERSONAL FORECAST: unavailable: {fc['error']}"]
    c, b, d = (fc[sc] for sc in SCENARIOS)
    worst = min((fc[sc] for sc in SCENARIOS), key=lambda x: x["lowest"])
    lines = []
    if fc["warning"]:
        lines += ["!" * 78, f"!! WARNING: cash falls to {R(worst['lowest'])} on {worst['lowest_on']} in the worst "
                  f"scenario, {R(worst['shortfall_to_reserve'])} short of your {R(fc['reserve'])} survival reserve.",
                  f"!! Below the reserve from {c['first_below_reserve'] or worst['first_below_reserve']} (conservative). "
                  "No extra debt repayment or discretionary spending is recommended.", "!" * 78]
    living = sum(i["max_paise"] for i in fc["living"])
    lines += [f"PERSONAL 30-DAY CASH FORECAST  {fc['today']} → {fc['end']}  (forecast, not actuals)",
              f"Opening cash: {R(fc['opening'])} (confirmed balance {fc['opening_as_of']} + later entries)",
              f"Living requirement (estimate, in the forecast): {R(living)} · Emergency reserve (kept, never spent): "
              f"{R(fc['reserve'])}",
              f"{'':<26}{'conservative':>17}{'best case':>17}{'salary +' + str(fc['salary_delay_days']) + 'd':>17}",
              *[f"{label:<26}{R(c[k]):>17}{R(b[k]):>17}{R(d[k]):>17}" for label, k in
                (("Inflows", "inflows"), ("Outflows", "outflows"), ("Lowest balance", "lowest"),
                 ("Closing balance", "closing"), ("Short of reserve", "shortfall_to_reserve"))],
              f"{'Lowest on':<26}{c['lowest_on']:>17}{b['lowest_on']:>17}{d['lowest_on']:>17}", "", "In the forecast:"]

    def item_line(i):
        amt = R(i["min_paise"]) + ("" if i["max_paise"] == i["min_paise"] else f"–{R(i['max_paise'])}")
        when = i["earliest"] + ("" if i["latest"] == i["earliest"] else f"…{i['latest']}")
        flags = ("" if i.get("confirmed", True) else " [UNCONFIRMED]") + (" [NEEDS YOUR ACTION]" if i["needs_action"] else "")
        out = [f"  {'+' if i['direction'] == 'inflow' else '-'} {i['label']}: {amt} ({when}){flags}  [{i['id']}]"]
        return out + ([f"      assumption: {i['assumption']}"] if i["assumption"] else [])

    for i in sorted(fc["items"], key=lambda i: i["earliest"]):
        lines += item_line(i)
    if fc["optional"]:
        lines.append("Optional, NOT in the scenarios (your choice, not a payment due):")
        for i in fc["optional"]:
            lines += item_line(i)
            w = fc["with_optional"][i["id"]]
            lines.append(f"      if you pay it: conservative lowest {R(w['lowest'])} on {w['lowest_on']}")
    if fc["debt_gaps"]:
        lines.append("Debt instalments NOT in the forecast (due date or EMI not entered): " + ", ".join(fc["debt_gaps"]))
    if fc["after_window"]:
        lines.append("After the window: " + ", ".join(i["label"] for i in fc["after_window"]))
    return lines


def summary_lines():
    R = store.rupees
    evs, problems = store.reconcile(validate)
    biz = business(evs)
    fc = forecast()
    cash, as_of, notes = personal_cash(evs=evs)
    rec = recommendation(biz, fc)
    lines = ["ACTUALS (owner-confirmed entries only)",
             f"Personal cash now: {R(cash) if cash is not None else 'unknown'}" + (f" (from balance on {as_of})" if as_of else ""),
             *[f"  note: {n}" for n in notes],
             "Business:",
             f"  cleared receipts {R(biz['cleared_receipts'])}  (client {R(biz['by_source']['client'])}, affiliate "
             f"{R(biz['by_source']['affiliate'])}, sponsorship {R(biz['by_source']['sponsorship'])}, other "
             f"{R(biz['by_source']['other'])})",
             f"  refunds {R(biz['refunds'])} · direct costs {R(biz['direct_costs'])} · operating expenses "
             f"{R(biz['operating_expenses'])}",
             f"  profit {R(biz['profit'])} · tax reserve {R(biz['tax_reserve'])} ({biz['tax_rate']:.0%} provisional "
             "estimate, not tax advice) · tax paid " + R(biz['tax_paid']),
             f"  withdrawals {R(biz['owner_withdrawals'])} · debt repayments {R(biz['debt_repayments'])} · business "
             f"cash {R(biz['business_cash'])} · operating reserve {R(biz['operating_reserve'])}",
             f"  NET AVAILABLE CASH: {R(max(0, biz['net_available']))}" + (
                 f" ({R(-biz['net_available'])} more business cash needed before anything is available: reserves "
                 "come first)" if biz["net_available"] < 0 else ""),
             *([f"  not counted (other currency, convert on receipt): {len(biz['other_currency'])} entr(y/ies)"]
               if biz["other_currency"] else []),
             f"Recommendation: {rec['action'].upper()}: {rec['why']}"
             + (f" → extra debt {R(rec['extra_debt'])}, buffer {R(rec['buffer'])}" if rec["action"] == "suggest" else ""),
             *([f"  {rec['note']}"] if rec.get("note") else []), ""]
    lines += forecast_lines(fc)
    from . import debt
    lines += ["", *debt.lines(evs), "", *debt.strategy_lines(rec.get("extra_debt", 0), not fc.get("warning", True))]
    if problems:
        lines += ["", f"LEDGER PROBLEMS ({len(problems)}; excluded from every total):",
                  *[f"  #{p['index']} {p['id'] or ''}: {p['problem']}" for p in problems]]
    return lines
