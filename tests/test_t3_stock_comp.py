"""T3: ASC 718 straight-line expense; forfeiture reverses unvested expense; nothing before board approval.

Grant used throughout: 4,800 shares x $2.50 = $12,000 over 48 months = $250.00 a month,
service from 15 Jan 2024, 12-month cliff."""
from datetime import date
from decimal import Decimal as D

from vouch import dates
from vouch.calc.stock import GrantTerms, cumulative_expense, month_expense, service_months

APPROVED = GrantTerms(4800, D("2.50"), date(2024, 2, 15), date(2024, 1, 15), 48, 12)


def expenses(terms, exit_date, first, last):
    return {m: month_expense(terms, exit_date, m) for m in dates.month_range(first, last)}


def test_service_months_count_monthly_anniversaries():
    assert service_months(date(2024, 1, 15), date(2024, 2, 14)) == 0
    assert service_months(date(2024, 1, 15), date(2024, 2, 15)) == 1
    assert service_months(date(2024, 1, 31), date(2024, 2, 29)) == 1      # short month: anniversary on its last day
    assert service_months(date(2024, 1, 15), date(2028, 1, 15)) == 48


def test_vesting_straight_line_with_catch_up_at_approval():
    months = expenses(APPROVED, None, "2024-01", "2024-04")
    assert months["2024-01"].expense == D("0.00")                     # not yet approved
    assert months["2024-02"].expense == D("250.00") and months["2024-02"].cumulative == D("250.00")
    assert months["2024-03"].expense == D("250.00") and months["2024-04"].cumulative == D("750.00")
    late = GrantTerms(4800, D("2.50"), date(2024, 5, 16), date(2024, 1, 15), 48, 12)
    assert month_expense(late, None, "2024-04").cumulative == D("0.00")
    assert month_expense(late, None, "2024-05").expense == D("1000.00")   # four months caught up on approval


def test_nothing_is_expensed_without_board_approval():
    unapproved = GrantTerms(4800, None, None, date(2024, 1, 15), 48, 12)
    for m in dates.month_range("2024-01", "2026-12"):
        result = month_expense(unapproved, None, m)
        assert result.expense == D("0.00") and result.status == "pending_approval"


def test_exit_before_the_cliff_reverses_everything():
    months = expenses(APPROVED, date(2024, 11, 20), "2024-02", "2025-01")
    assert months["2024-10"].cumulative == D("2250.00")
    assert months["2024-11"].expense == D("-2250.00") and months["2024-11"].cumulative == D("0.00")
    assert months["2024-11"].status == "forfeited" and months["2024-11"].vested_shares == 0
    assert months["2024-12"].expense == D("0.00")
    assert sum(m.expense for m in months.values()) == D("0.00")


def test_exit_after_the_cliff_keeps_only_the_vested_part():
    months = expenses(APPROVED, date(2025, 6, 30), "2024-02", "2025-08")
    assert months["2025-06"].cumulative == D("4250.00")               # 17 months x 250
    assert months["2025-06"].vested_shares == 1700 and months["2025-06"].status == "forfeited"
    assert months["2025-07"].expense == D("0.00") and months["2025-08"].cumulative == D("4250.00")


def test_full_vest_expenses_the_whole_value_and_then_nothing():
    months = expenses(APPROVED, None, "2024-01", "2028-03")
    assert months["2028-01"].cumulative == D("12000.00") and months["2028-01"].status == "fully_vested"
    assert months["2028-02"].expense == D("0.00")
    assert sum(m.expense for m in months.values()) == D("12000.00")
    after = month_expense(APPROVED, date(2028, 2, 10), "2028-02")
    assert after.status == "vested_and_left" and after.expense == D("0.00") and after.vested_shares == 4800


def test_rounding_telescopes_to_the_exact_grant_value():
    awkward = GrantTerms(1000, D("3.33"), date(2024, 2, 15), date(2024, 1, 15), 48, 12)   # 3,330 / 48 = 69.375
    months = expenses(awkward, None, "2024-01", "2028-02")
    assert sum(m.expense for m in months.values()) == D("3330.00")
    assert cumulative_expense(awkward, None, date(2028, 1, 31))[3] == D("3330.00")
