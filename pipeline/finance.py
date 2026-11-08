"""Finance agent: a plain CSV ledger and the two numbers that must never be confused.

  Operating profit            = recognised revenue − refunds − operating expenses
  Debt-repayment cash         = cash received − refunds − expenses paid − outstanding obligations
                                − required reserve − tax provision

Only rows you enter (or that come from real statements) are counted. Nothing is projected here; scenario
models live in docs/REVENUE_AND_UNIT_ECONOMICS.md and are labelled hypothetical.

Ledger: data/finance/ledger.csv
  date, kind (revenue|expense|refund), category, amount, currency, status (earned|received|paid|owed), note
"""
import csv
import datetime as dt

from .util import DATA, ROOT, log

LEDGER = DATA / "finance" / "ledger.csv"
FIELDS = ["date", "kind", "category", "amount", "currency", "status", "note"]


def rows():
    if not LEDGER.exists():
        return []
    with open(LEDGER, newline="") as f:
        return list(csv.DictReader(f))


def add(kind, category, amount, status, note="", currency="INR", date=None):
    if kind not in ("revenue", "expense", "refund"):
        raise SystemExit("kind must be revenue, expense or refund")
    if status not in ("earned", "received", "paid", "owed"):
        raise SystemExit("status must be earned, received, paid or owed")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    with open(LEDGER, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({"date": date or dt.date.today().isoformat(), "kind": kind, "category": category,
                    "amount": f"{float(amount):.2f}", "currency": currency, "status": status, "note": note})
    log(f"ledger: {kind} {amount} {currency} ({category}, {status})")


def compute(cfg, ledger=None):
    f = cfg.get("finance", {})
    L = ledger if ledger is not None else rows()
    amt = lambda r: float(r["amount"])
    revenue_recognised = sum(amt(r) for r in L if r["kind"] == "revenue" and r["status"] in ("earned", "received"))
    revenue_received = sum(amt(r) for r in L if r["kind"] == "revenue" and r["status"] == "received")
    revenue_unpaid = revenue_recognised - revenue_received
    refunds = sum(amt(r) for r in L if r["kind"] == "refund")
    expenses = sum(amt(r) for r in L if r["kind"] == "expense")
    expenses_paid = sum(amt(r) for r in L if r["kind"] == "expense" and r["status"] == "paid")
    expenses_owed = expenses - expenses_paid
    operating_profit = revenue_recognised - refunds - expenses
    tax_provision = max(0.0, operating_profit) * f.get("tax_rate", 0.30)
    reserve = f.get("reserve", 0.0)
    obligations = expenses_owed + f.get("other_obligations", 0.0)
    debt_cash = max(0.0, revenue_received - refunds - expenses_paid - obligations - reserve - tax_provision)
    return {"revenue_recognised": revenue_recognised, "revenue_received": revenue_received,
            "revenue_earned_unpaid": revenue_unpaid, "refunds": refunds, "expenses": expenses,
            "expenses_paid": expenses_paid, "obligations": obligations, "operating_profit": operating_profit,
            "tax_provision": tax_provision, "reserve": reserve, "debt_repayment_cash": debt_cash,
            "currency": f.get("currency", "INR")}


def summary_lines(cfg):
    """Channel daily reports are committed to git, so they carry no money figures (Phase 7, 2 Nov): owner finance
    lives in git-ignored data/business/ and is shown only in the terminal (`main.py money`)."""
    return ["## Money", "- Owner finance is private: run `python main.py money` (data/business/, git-ignored). "
            "This per-channel ledger is retired.", ""]


def legacy_summary_lines(cfg):
    c = compute(cfg)
    cur = c["currency"]
    fmt = lambda k: f"{c[k]:,.2f} {cur}"
    try:
        where = LEDGER.relative_to(ROOT)
    except ValueError:
        where = LEDGER
    return [f"## Money (from {where} — actual entries only)",
            f"- Revenue recognised: {fmt('revenue_recognised')} (received {fmt('revenue_received')}, "
            f"earned-not-paid {fmt('revenue_earned_unpaid')})",
            f"- Expenses: {fmt('expenses')}  · Refunds: {fmt('refunds')}",
            f"- **Operating profit: {fmt('operating_profit')}**",
            f"- Tax provision ({cfg.get('finance', {}).get('tax_rate', 0.30):.0%} estimate, verify with a tax "
            f"professional): {fmt('tax_provision')} · Reserve: {fmt('reserve')} · Obligations: {fmt('obligations')}",
            f"- **Cash available for debt repayment: {fmt('debt_repayment_cash')}**", ""]
