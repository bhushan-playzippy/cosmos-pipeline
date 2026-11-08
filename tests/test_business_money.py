"""Phase 7 A+B: owner-level store (paise, ids, idempotency, atomic + strict reads, reconciliation) and the personal
and business books (net available cash, 30-day forecast, recommendation gating). Sandbox only: store.BASE is a
tmp folder (conftest), nothing touches real money data."""
import datetime as dt
import json

import pytest

from pipeline.business import money, store
from pipeline.util import CorruptState

TODAY = dt.date(2026, 11, 2)
R = 100  # paise per rupee


# ---------------------------------------------------------------- A. store

def test_amounts_are_integer_paise_parsed_from_text():
    assert store.paise("1,22,000") == 12_200_000 and store.paise("₹1,234.5") == 123_450 and store.paise(40000) == 4_000_000
    for bad in ("12.345", "abc", "", None, True, "1e5"):
        with pytest.raises(ValueError):
            store.paise(bad)
    assert store.rupees(12_200_000) == "₹1,22,000.00" and store.rupees(-50_000) == "-₹500.00"
    assert store.rupees(1_000_000, "USD") == "10,000.00 USD"


def test_ledger_lives_outside_the_channel_folders_and_is_owner_level(sandbox):
    from pipeline import channels
    money.add("personal", "balance", 8000, "owner statement", True, date="2026-11-02")
    channels.activate(channels.load("clips"))  # switching channel must not move or split owner money
    assert store.path("ledger.json") == sandbox / "data" / "business" / "ledger.json"
    assert len(store.events()) == 1


def test_same_entry_twice_is_recorded_once_and_a_conflicting_key_is_refused():
    a, new_a = money.add("business", "receipt", 7500, "UPI 123", True, category="client", date="2026-11-03")
    b, new_b = money.add("business", "receipt", 7500, "UPI 123", True, category="client", date="2026-11-03")
    assert new_a and not new_b and a["id"] == b["id"] and len(store.events()) == 1
    with pytest.raises(ValueError, match="already used"):
        money.add("business", "receipt", 9999, "UPI 123", True, category="client", date="2026-11-03", key=a["key"])


def test_writes_are_atomic_with_backup_and_damage_is_never_read_as_empty():
    money.add("personal", "balance", 8000, "stmt", True, date="2026-11-01")
    money.add("personal", "expense", 500, "cash", True, category="food", date="2026-11-01")
    p = store.path("ledger.json")
    assert p.with_name("ledger.json.bak").exists()
    p.write_text("{broken")
    with pytest.raises(CorruptState):
        store.events()
    p.unlink()  # an interrupted write leaves only the .bak: still not "no money"
    with pytest.raises(CorruptState, match="missing"):
        store.events()


def test_malformed_rows_are_reported_and_excluded_never_counted():
    money.add("business", "receipt", 1000, "r1", True, category="client", date="2026-11-03")
    data = json.loads(store.path("ledger.json").read_text())
    data["events"] += [{"id": "x1", "key": "k1", "book": "business", "type": "receipt", "category": "client",
                        "date": "2026-11-03", "amount_paise": 99.5, "currency": "INR", "ref": "r", "confirmed": True},
                       {"id": "x2", "key": "k2", "book": "business", "type": "invoice_sent", "date": "2026-11-03",
                        "amount_paise": 500000, "currency": "INR", "ref": "r", "confirmed": True},
                       "garbage"]
    store.save("ledger.json", data)
    good, problems = store.reconcile(money.validate)
    assert len(good) == 1 and len(problems) == 3
    assert money.business(good)["cleared_receipts"] == 100_000


def test_currencies_are_never_added_together():
    money.add("business", "receipt", 7500, "r1", True, category="client", date="2026-11-03")
    money.add("business", "receipt", 50, "paypal 9", True, category="affiliate", date="2026-11-03", currency="USD")
    b = money.business()
    assert b["cleared_receipts"] == 750_000 and len(b["other_currency"]) == 1


# ---------------------------------------------------------------- B. books and rules

def test_every_money_entry_needs_confirmation_and_an_external_reference():
    with pytest.raises(ValueError, match="confirmation"):
        money.add("business", "receipt", 7500, "UPI 1", False, category="client")
    with pytest.raises(ValueError, match="reference"):
        money.add("business", "receipt", 7500, "  ", True, category="client")
    assert store.events() == []


def test_salary_is_never_business_revenue_and_pending_money_has_no_entry_type():
    with pytest.raises(ValueError):
        money.add("business", "receipt", 122000, "salary", True, category="salary")
    with pytest.raises(ValueError):
        money.add("business", "invoice", 15000, "INV-1", True)  # an unpaid invoice is not a money event
    money.add("personal", "income", 122000, "NEFT 55", True, category="salary", date="2026-12-01")
    assert money.business()["cleared_receipts"] == 0


def test_debt_and_borrowing_entries_name_the_account():
    from pipeline.business import debt
    with pytest.raises(ValueError, match="account"):
        money.add("personal", "debt_payment", 40000, "NEFT 1", True)
    with pytest.raises(ValueError, match="no debt account"):  # Phase C: the account must exist
        money.add("personal", "debt_payment", 40000, "NEFT 1", True, account="loan-a", date="2026-11-10")
    debt.upsert("loan-a", lender="Test Bank")
    money.add("personal", "debt_payment", 40000, "NEFT 1", True, account="loan-a", date="2026-11-10")


def test_business_net_available_cash_formula(monkeypatch):
    # D1 (paid operating expenses count), D3 (30% of cleared profit), D4 (₹5,000 operating reserve)
    money.add("business", "receipt", 15000, "UPI a", True, category="client", date="2026-11-05")
    money.add("business", "refund", 1000, "UPI b", True, date="2026-11-06")
    money.add("business", "direct_cost", 2000, "inv 7", True, date="2026-11-06")
    money.add("business", "operating_expense", 500, "inv 8", True, date="2026-11-06")
    b = money.business()
    assert b["profit"] == 1_150_000
    assert b["tax_reserve"] == 345_000                      # 30% of ₹11,500
    assert b["business_cash"] == 1_150_000
    assert b["net_available"] == 1_150_000 - 345_000 - 500_000  # ₹3,050
    money.add("business", "tax_payment", 1000, "challan 1", True, date="2026-11-07")
    assert money.business()["tax_reserve"] == 245_000       # tax actually paid reduces what's still owed


def test_withdrawal_moves_money_between_books_once():
    money.add("personal", "balance", 1000, "stmt", True, date="2026-11-01")
    money.add("business", "receipt", 10000, "UPI c", True, category="client", date="2026-11-02")
    money.withdraw(3000, "IMPS 9", True, date="2026-11-03")
    money.withdraw(3000, "IMPS 9", True, date="2026-11-03")  # re-run: no double count
    assert money.business()["business_cash"] == 700_000
    assert money.personal_cash("2026-11-03")[0] == 400_000


def owner_november():
    """The owner's 2 Nov inputs, as entered for real (see the Phase A+B report)."""
    money.add("personal", "balance", 8000, "owner statement 2 Nov", True, date="2026-11-02")
    money.plan("Basic living costs until salary", "outflow", 13000, "2026-11-02", latest="2026-11-30", spread=True,
               category="essential_other", assumption="₹8,000 cash + ₹5,000 reported shortfall")
    money.plan("Loan payment", "outflow", 40000, "2026-11-10", category="debt", needs_action=True)
    money.plan("Repay friend", "outflow", 20000, "2026-11-20", amount_max=25000, latest="2026-11-26",
               category="friend", needs_action=True)
    money.plan("Salary", "inflow", 122000, "2026-12-01", latest="2026-12-03", category="salary",
               assumption="'around the beginning of next month'")
    money.plan("Monthly debt instalments", "outflow", 60000, "2026-12-01", latest="2026-12-05", category="debt",
               assumption="dates unknown; assumed after the salary")


def test_november_forecast_shows_the_shortfall_and_both_scenarios():
    owner_november()
    fc = money.forecast(TODAY, 30)
    c, b = fc["conservative"], fc["best"]
    # conservative: friend ₹25,000 on 20 Nov, salary after the window, instalments inside it
    assert c["lowest"] == (8000 - 13000 - 40000 - 25000 - 60000) * R
    # best: friend ₹20,000 on 26 Nov, salary 1 Dec, instalments after the window
    assert b["lowest"] == (8000 - 13000 - 40000 - 20000) * R and b["closing"] == (8000 - 13000 - 40000 - 20000 + 122000) * R
    assert fc["warning"] and c["first_below_reserve"] is not None
    assert {i["label"] for i in fc["needs_action"]} == {"Loan payment", "Repay friend"}
    assert "WARNING" in "\n".join(money.forecast_lines(fc))


def test_the_40k_loan_and_the_monthly_instalments_are_separate_items():
    owner_november()
    labels = [i["label"] for i in money.plan_items()]
    assert labels.count("Loan payment") == 1 and labels.count("Monthly debt instalments") == 1


def test_no_extra_repayment_or_spending_recommended_while_the_personal_forecast_is_negative():
    owner_november()
    money.add("business", "receipt", 30000, "UPI big", True, category="client", date="2026-11-03")
    rec = money.recommendation(money.business(), money.forecast(TODAY))
    assert rec["action"] == "hold" and rec["extra_debt"] == 0 and rec["withdrawable"] > 0


def test_recommendation_splits_net_cash_only_when_the_personal_forecast_is_safe():
    money.add("personal", "balance", 50000, "stmt", True, date="2026-11-02")
    money.add("business", "receipt", 30000, "UPI d", True, category="client", date="2026-11-03")
    rec = money.recommendation(money.business(), money.forecast(TODAY))
    avail = 3_000_000 - 900_000 - 500_000
    assert rec["action"] == "suggest" and rec["extra_debt"] == avail // 2 and rec["buffer"] == avail - avail // 2


def test_forecast_requires_an_opening_balance():
    assert "opening balance" in money.forecast(TODAY)["error"]
    assert money.recommendation()["action"] == "hold"


def test_plan_items_are_idempotent_and_never_cash():
    money.plan("Loan payment", "outflow", 40000, "2026-11-10")
    _, again = money.plan("Loan payment", "outflow", 40000, "2026-11-10")
    assert not again and len(money.plan_items()) == 1 and store.events() == []
    with pytest.raises(ValueError, match="different values"):
        money.plan("Loan payment", "outflow", 45000, "2026-11-10")


def test_cli_money_add_is_idempotent_and_refuses_without_confirmation(monkeypatch, capsys):
    import sys
    import main
    args = ["main.py", "money", "add", "--book", "business", "--kind", "receipt", "--category", "client",
            "--amount", "7500", "--ref", "UPI 77", "--date", "2026-11-04"]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit, match="confirmation"):
        main.main()
    for _ in range(2):
        monkeypatch.setattr(sys, "argv", args + ["--confirm"])
        main.main()
    assert len(store.events()) == 1 and "already recorded" in capsys.readouterr().out


def test_channel_daily_report_carries_no_money_figures(cfg, fixed_now):
    from pipeline import analytics
    money.add("business", "receipt", 7500, "UPI 5", True, category="client", date="2026-11-03")
    analytics.write_daily_report(cfg)
    text = next(analytics.REPORTS.rglob("daily-*.md")).read_text()
    assert "7,500" not in text and "money" in text.lower()
