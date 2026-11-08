"""Phase 7 C: debt accounts, instalment status, duplicate-payment protection, reversals, lender-statement
reconciliation, strategies, and the corrected personal forecast (optional foreclosure, delayed salary, EMIs only
with known dates). Sandbox only: nothing pays, forecloses or contacts anyone."""
import datetime as dt

import pytest

from pipeline.business import debt, money, store

TODAY = dt.date(2026, 11, 2)
R = 100


def pay(account, amount, ref, date):
    return money.add("personal", "debt_payment", amount, ref, True, account=account, date=date)


# ---------------------------------------------------------------- accounts

def test_account_stores_only_what_was_entered_and_masks_the_number():
    acc, _ = debt.upsert("kotak-pl", lender="Kotak", account_ref="1234-5678-9012", foreclosure_quote_paise="40000")
    assert acc["account_ref"] == "XXXX9012"
    for unknown in ("principal_paise", "rate_annual_pct", "emi_paise", "next_due", "due_day"):
        assert acc.get(unknown) is None
    assert acc["rate_type"] == "unknown" and acc["statement_status"] == "unverified"
    text = "\n".join(debt.lines(today=TODAY))
    assert "outstanding unknown" in text and "rate unknown" in text and "NOT confirmed by the lender" in text
    assert "1234" not in text


def test_corrections_keep_the_old_values():
    debt.upsert("card", lender="Bank", emi_paise="5000")
    acc, changed = debt.upsert("card", emi_paise="6000")
    assert changed and acc["changes"][-1]["fields"]["emi_paise"] == [500_000, 600_000]
    assert not debt.upsert("card", emi_paise="6000")[1]


def test_new_account_needs_a_lender_and_a_valid_id():
    with pytest.raises(ValueError):
        debt.upsert("x y", lender="A")
    with pytest.raises(ValueError, match="lender"):
        debt.upsert("ok-id")


# ---------------------------------------------------------------- instalments and statuses

def test_instalment_statuses():
    debt.upsert("loan", lender="Bank", emi_paise="10000", next_due="2026-10-20", due_day=20)
    pay("loan", 10000, "NEFT oct", "2026-10-19")   # October paid (a day early)
    pay("loan", 4000, "NEFT nov", "2026-11-01")    # part of November... allocated oldest-first
    st = {i["due"]: i["status"] for i in debt.instalments(debt.get("loan"), dt.date(2027, 1, 31), TODAY)}
    assert st["2026-10-20"] == "paid"
    assert st["2026-11-20"] == "partially_paid"
    assert st["2026-12-20"] == "scheduled" and st["2027-01-20"] == "scheduled"
    debt.upsert("late", lender="Bank", emi_paise="5000", next_due="2026-10-25")
    debt.upsert("soon", lender="Bank", emi_paise="5000", next_due="2026-11-05")
    assert debt.instalments(debt.get("late"), TODAY, TODAY)[0]["status"] == "overdue"
    assert debt.instalments(debt.get("soon"), dt.date(2026, 11, 6), TODAY)[0]["status"] == "due"


def test_an_earlier_payment_does_not_settle_the_next_instalment():
    """The October EMI already paid must not mark November paid (owner, 2 Nov: don't count the paid ₹60,000)."""
    debt.upsert("emi", lender="Bank", emi_paise="60000", next_due="2026-12-01")
    pay("emi", 60000, "NEFT oct-emi", "2026-10-30")
    assert debt.instalments(debt.get("emi"), dt.date(2026, 12, 2), TODAY)[0]["status"] == "scheduled"


def test_reversed_payment_keeps_history_and_reopens_the_instalment():
    debt.upsert("loan", lender="Bank", emi_paise="10000", next_due="2026-10-20")
    ev, _ = pay("loan", 10000, "NEFT bounced", "2026-10-20")
    money.reverse(ev["id"], "bank return 77", True, date="2026-10-22")
    assert len(store.events()) == 2  # nothing edited or deleted
    live, rev, _ = debt.payments("loan")
    assert live == [] and rev[0]["id"] == ev["id"]
    assert debt.instalments(debt.get("loan"), TODAY, TODAY)[0]["status"] == "reversed"
    with pytest.raises(ValueError, match="already reversed"):
        money.reverse(ev["id"], "again", True)


# ---------------------------------------------------------------- duplicate protection

def test_a_payment_cannot_be_counted_twice():
    debt.upsert("loan", lender="Bank")
    pay("loan", 15000, "UTR 111", "2026-11-05")
    pay("loan", 15000, "UTR 111", "2026-11-05")  # the same command again: no-op
    with pytest.raises(ValueError, match="already used"):
        pay("loan", 51000, "UTR 111", "2026-11-05")  # same bank reference, typo in the amount
    assert len(debt.payments("loan")[0]) == 1


def test_same_amount_twice_within_three_days_is_flagged_for_review():
    debt.upsert("loan", lender="Bank")
    pay("loan", 15000, "UTR 1", "2026-11-05")
    pay("loan", 15000, "UTR 2", "2026-11-06")
    assert any("possible duplicate" in i for i in debt.reconcile("loan"))


# ---------------------------------------------------------------- lender statements

def test_statement_reconciliation_flags_every_discrepancy():
    debt.upsert("loan", lender="Bank", principal_paise="100000", principal_as_of="2026-10-31")
    pay("loan", 10000, "UTR a", "2026-10-05")
    pay("loan", 7000, "UTR b", "2026-10-25")  # not on the statement
    debt.add_statement("loan", "2026-10-31", "95000", [("2026-10-06", "10000"), ("2026-10-15", "3000")],
                       source="Bank PDF Oct")
    issues = debt.reconcile("loan")
    assert debt.get("loan")["statement_status"] == "verified"
    assert any("₹3,000.00 on 2026-10-15" in i and "doesn't have" in i for i in issues)
    assert any("₹7,000.00" in i and "not on the" in i for i in issues)
    assert any("differs from the statement" in i for i in issues)
    assert debt.balance(debt.get("loan"))[0] == 9_500_000  # the statement wins; nothing auto-corrected
    _, again = debt.add_statement("loan", "2026-10-31", "95000", source="Bank PDF Oct")
    assert not again and len(debt.get("loan")["statements"]) == 1


def test_statement_needs_a_source():
    debt.upsert("loan", lender="Bank")
    with pytest.raises(ValueError, match="source"):
        debt.add_statement("loan", "2026-10-31", "1000")


def test_aggregate_estimate_is_never_added_twice():
    debt.upsert("aggregate", lender="All lenders (owner estimate)", kind="aggregate", principal_paise="400000")
    debt.upsert("kotak-pl", lender="Kotak", in_aggregate=True, principal_paise="38000")
    debt.upsert("friend", lender="Friend", kind="personal", principal_paise="25000")
    t = debt.total()
    assert t["known_paise"] == (400000 + 25000) * R and t["covered_by_aggregate"] == ["kotak-pl"]


# ---------------------------------------------------------------- strategies

def verified(acc_id, bal, rate, emi):
    debt.upsert(acc_id, lender=acc_id, rate_annual_pct=rate, rate_type="reducing", emi_paise=str(emi))
    debt.add_statement(acc_id, "2026-10-31", str(bal), source=f"{acc_id} statement")


def test_avalanche_and_snowball_orders_and_impact_only_with_verified_data():
    verified("card", 50000, 36, 5000)
    verified("car", 200000, 10, 8000)
    s = debt.strategies(extra_paise=2000 * R)
    assert s["avalanche"] == ["card", "car"] and s["snowball"] == ["card", "car"]
    base, ava = s["impact"]["minimum_only"], s["impact"]["avalanche"]
    assert ava[0] < base[0] and ava[1] < base[1]  # extra repayment clears sooner with less interest
    debt.upsert("new", lender="X", emi_paise="1000")  # rate and balance unknown
    s = debt.strategies(extra_paise=2000 * R)
    assert s["impact"] is None and "new" in s["missing"] and s["avalanche"] is None


def test_no_additional_repayment_recommended_below_the_survival_reserve():
    text = "\n".join(debt.strategy_lines(5000 * R, personal_safe=False))
    assert "NOT RECOMMENDED" in text


# ---------------------------------------------------------------- corrected personal forecast

def november():
    money.add("personal", "balance", 8000, "owner statement", True, date="2026-11-02")
    money.plan("Living costs", "outflow", 13000, "2026-11-02", latest="2026-11-30", spread=True,
               category="essential_other")
    money.plan("Kotak foreclosure (quote)", "outflow", 40000, "2026-11-10", optional=True, confirmed=False,
               needs_action=True)
    money.plan("Friend repayment", "outflow", 25000, "2026-11-02", latest="2026-11-26", category="friend",
               needs_action=True)
    money.plan("Salary", "inflow", 122000, "2026-12-01", latest="2026-12-03", category="salary", confirmed=False)
    debt.upsert("aggregate", lender="All lenders (estimate)", kind="aggregate", principal_paise="400000",
                emi_paise="60000")


def test_foreclosure_quote_is_optional_and_out_of_every_scenario():
    november()
    fc = money.forecast(TODAY)
    assert all(i["label"] != "Kotak foreclosure (quote)" for i in fc["items"])
    assert fc["conservative"]["lowest"] == (8000 - 13000 - 25000) * R
    assert fc["with_optional"][fc["optional"][0]["id"]]["lowest"] == (8000 - 13000 - 25000 - 40000) * R
    text = "\n".join(money.forecast_lines(fc))
    assert "NOT in the scenarios" in text and "[UNCONFIRMED]" in text


def test_delayed_salary_scenario_and_living_shown_apart_from_the_reserve():
    november()
    fc = money.forecast(TODAY, days=35)
    assert fc["best"]["inflows"] == 122000 * R            # salary 1 Dec inside 35 days
    assert fc["conservative"]["inflows"] == 122000 * R    # 3 Dec still inside
    assert fc["delayed_salary"]["inflows"] == 0           # 10 Dec: outside the window
    text = "\n".join(money.forecast_lines(fc))
    assert "Living requirement (estimate, in the forecast): ₹13,000.00" in text
    assert "Emergency reserve (kept, never spent): ₹5,000.00" in text and "salary +7d" in text


def test_instalments_without_known_dates_are_listed_not_guessed():
    november()
    fc = money.forecast(TODAY)
    assert fc["debt_gaps"] == ["aggregate"] and not any(i["id"].startswith("emi-") for i in fc["items"])
    debt.upsert("bank-loan", lender="Bank", emi_paise="20000", next_due="2026-11-15")
    fc = money.forecast(TODAY)
    assert [i["label"] for i in fc["items"] if i["id"].startswith("emi-")] == ["EMI Bank (scheduled)"]


def test_plan_corrections_keep_their_history():
    item, _ = money.plan("Friend repayment", "outflow", 20000, "2026-11-02", amount_max=25000, latest="2026-11-26")
    money.plan_set(item["id"], "owner: track the full ₹25,000", min_paise=2_500_000)
    got = next(i for i in money.plan_items() if i["id"] == item["id"])
    assert got["min_paise"] == 2_500_000 and got["changes"][0]["fields"]["min_paise"] == [2_000_000, 2_500_000]
    with pytest.raises(ValueError, match="why"):
        money.plan_set(item["id"], "", min_paise=1)


def test_cli_debt_add_masks_and_refuses_duplicates(monkeypatch, capsys):
    import sys
    import main
    base = ["main.py", "money", "debt-add", "--account", "kotak-pl", "--lender", "Kotak", "--account-ref",
            "99887766554433", "--foreclosure", "40000"]
    monkeypatch.setattr(sys, "argv", base)
    main.main()
    with pytest.raises(SystemExit, match="exists"):
        main.main()
    assert debt.get("kotak-pl")["account_ref"] == "XXXX4433"
    assert "99887766554433" not in store.path("debts.json").read_text()
