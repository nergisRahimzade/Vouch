"""B4: payroll (per person per period) and equity (per grant per month) subledgers roll up to
the general ledger in every booked month."""
from decimal import Decimal

import pytest

from vouch.db import subledger
from vouch.db.ledger import Entry, Line, SourceRef, post_entry
from vouch.world import config as C


def test_subledgers_hold_every_source_row(db):
    registers = db.execute("SELECT COUNT(*) FROM v_payroll_register").fetchone()[0]
    grants = db.execute("SELECT COUNT(*) FROM v_equity_report").fetchone()[0]
    assert db.execute("SELECT COUNT(*) FROM payroll_subledger").fetchone()[0] == registers > 3000
    assert db.execute("SELECT COUNT(*) FROM equity_subledger").fetchone()[0] == grants > 3000


def test_subledger_totals_equal_the_ledger_for_every_booked_month(db):
    booked = subledger.booked_periods(db)
    closed = {(p, e) for p in [r[0] for r in db.execute("SELECT period FROM periods WHERE status = 'locked'")]
              for e in ("US", "UK", "DE")}
    assert booked == closed
    assert subledger.rollup(db) == []


def test_open_month_is_not_yet_booked_and_has_no_translated_stock_expense(db):
    assert C.OPEN_MONTH not in {p for p, _ in subledger.booked_periods(db)}
    assert db.execute("SELECT COUNT(*) FROM payroll_subledger WHERE period = ?", (C.OPEN_MONTH,)).fetchone()[0] > 290
    untranslated = db.execute("SELECT COUNT(*), COUNT(expense_local) FROM equity_subledger WHERE period = ?",
                              (C.OPEN_MONTH,)).fetchone()
    assert untranslated[0] > 0 and untranslated[1] == 0


def test_a_ledger_that_disagrees_with_its_subledger_is_reported(scratch_db):
    doc, row = scratch_db.execute("SELECT doc_id, row_no FROM v_payroll_register WHERE month = ? AND entity = 'US'",
                                  (C.OPEN_MONTH,)).fetchone()
    post_entry(scratch_db, Entry(
        "TEST-WRONG-ACCRUAL", "US", C.OPEN_MONTH, f"{C.OPEN_MONTH}-28", "accrual with a wrong amount",
        [Line("6000", debit=Decimal("100.00"), department="Engineering", sources=[SourceRef(doc, row)]),
         Line("2100", credit=Decimal("100.00"), sources=[SourceRef(doc, row)])], "test", status="posted"))
    differences = subledger.rollup(scratch_db)
    assert differences and {d.period for d in differences} == {C.OPEN_MONTH}
    wages = [d for d in differences if d.account == "6000" and d.department == "Engineering" and d.entity == "US"]
    assert wages and wages[0].ledger == Decimal("100.00") and wages[0].subledger > Decimal("100.00")


def test_a_register_amount_without_a_posting_rule_is_refused():
    with pytest.raises(subledger.UnmappedColumn):
        subledger._register_amounts({"employee_id": "x", "employee_name": "y", "employer_new_levy": "1.00"})
