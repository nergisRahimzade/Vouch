"""T4: real recorded rates; one rate per month (the monthly average, policy FIN-02);
every converted amount names its rate and the dates it comes from."""
from decimal import Decimal as D

import pytest

from vouch.calc import fx
from vouch.db.ledger import Entry, EntryRefused, Line, SourceRef, post_entry
from vouch.world import config as C


def test_translation_names_its_rate_month_and_days():
    t = fx.translate(D("1000.00"), "GBP", "2026-03")
    assert t.amount_local == fx.to_local(D("1000.00"), t.rate)
    assert "2026-03" in t.basis and "monthly average" in t.basis and "Open Exchange Rates" in t.basis
    assert f"{t.rate.first_date}..{t.rate.last_date}" in t.basis and t.rate.days == len(fx.business_days("2026-03"))


def test_expenses_and_balances_use_the_same_monthly_average():
    expense = fx.translate(D("500.00"), "EUR", "2026-06", "expense")
    balance = fx.translate(D("500.00"), "EUR", "2026-06", "balance")
    assert expense.rate == balance.rate and expense.amount_local == balance.amount_local
    with pytest.raises(ValueError):
        fx.translate(D("1"), "EUR", "2026-06", "spot")


def test_a_month_without_every_business_day_is_held_not_guessed():
    with pytest.raises(fx.MissingFxRate, match="no recorded rate"):
        fx.translate(D("1"), "GBP", "2030-02")


def test_every_booked_conversion_names_the_policy_rate(world):
    checked = 0
    for month in world.months():
        for line in world.csv(f"months/{month}/ledger_booked.csv"):
            if not line["fx_rate"] or line["entry_id"].endswith("-OPENING"):
                continue
            currency = line["currency"]
            rate = fx.month_average(currency, line["period"])
            assert line["fx_rate"] == str(rate.rate) and line["fx_rate_basis"] == rate.citation, line
            checked += 1
    assert checked > 50


def test_every_platform_conversion_names_a_basis(world):
    for month in world.months():
        for row in world.csv(f"months/{month}/equity_report.csv"):
            assert row["fx_rate"] and month in row["fx_rate_basis"], row


def test_opening_capital_names_the_cutover_rate(world):
    for row in world.csv("standing/opening_balances.csv"):
        if row["account"] == "3100" and row["currency"] != "USD":
            assert row["fx_rate"] and "monthly average" in row["basis"] and row["fx_rate"] in row["basis"]


def test_ledger_stores_the_basis_and_refuses_a_rate_without_one(db, scratch_db):
    assert db.execute("SELECT COUNT(*) FROM journal_lines WHERE fx_rate IS NOT NULL AND "
                      "(fx_basis IS NULL OR fx_basis = '')").fetchone()[0] == 0
    doc, row = scratch_db.execute("SELECT doc_id, row_no FROM v_equity_report WHERE month = ? LIMIT 1",
                                  (C.OPEN_MONTH,)).fetchone()
    entry = Entry("TEST-FX", "UK", C.OPEN_MONTH, f"{C.OPEN_MONTH}-30", "stock comp", [
        Line("6300", debit=D("10.00"), department="GTM", fx_rate="0.75", sources=[SourceRef(doc, row)]),
        Line("3100", credit=D("10.00"), fx_rate="0.75", sources=[SourceRef(doc, row)])], "Vouch")
    with pytest.raises(EntryRefused, match="must name its rate"):
        post_entry(scratch_db, entry)
