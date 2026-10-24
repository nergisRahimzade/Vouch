"""B5: closed months are locked; corrections are booked in the open month; only the
controller can reopen."""
import sqlite3
from decimal import Decimal

import pytest

from vouch.db import periods
from vouch.db.ledger import Entry, Line, SourceRef
from vouch.world import config as C

CLOSED = "2026-05"


def _entry(conn, period, entry_id="TEST-CORRECTION"):
    doc, row = conn.execute("SELECT doc_id, row_no FROM v_payroll_register WHERE month = ? AND entity = 'UK'",
                            (period,)).fetchone()
    return Entry(entry_id, "UK", period, f"{period}-20", "employer pension shortfall",
                 [Line("6200", debit=Decimal("10.00"), department="GTM", sources=[SourceRef(doc, row)]),
                  Line("2220", credit=Decimal("10.00"), sources=[SourceRef(doc, row)])], "Vouch")


def test_closed_months_are_locked_by_the_controller(db):
    locked = [r[0] for r in db.execute("SELECT period FROM periods WHERE status = 'locked' ORDER BY period")]
    assert len(locked) == 12 and C.OPEN_MONTH not in locked
    events = db.execute("SELECT DISTINCT actor, role, action FROM period_events").fetchall()
    assert [tuple(e) for e in events] == [("Amara Okafor", "controller", "lock")]


def test_writing_to_a_locked_month_is_refused(scratch_db):
    with pytest.raises(periods.PeriodLocked, match="book the correction in the open month"):
        periods.post(scratch_db, _entry(scratch_db, CLOSED))
    assert scratch_db.execute("SELECT COUNT(*) FROM journal_entries WHERE entry_id = 'TEST-CORRECTION'").fetchone()[0] == 0


def test_the_database_itself_refuses_locked_writes(scratch_db):
    entry_id = scratch_db.execute("SELECT entry_id FROM journal_entries WHERE period = ? LIMIT 1", (CLOSED,)).fetchone()[0]
    statements = [
        ("INSERT INTO journal_entries VALUES ('X', 'US', ?, ?, 'r', 'USD', 'posted', 'vouch', 'v', NULL, 'now')",
         (CLOSED, f"{CLOSED}-01")),
        ("UPDATE journal_lines SET debit = '0.00' WHERE entry_id = ?", (entry_id,)),
        ("DELETE FROM journal_lines WHERE entry_id = ?", (entry_id,)),
        ("DELETE FROM journal_entries WHERE entry_id = ?", (entry_id,)),
        ("UPDATE journal_entries SET period = ? WHERE entry_id = ?", (C.OPEN_MONTH, entry_id)),
    ]
    for sql, params in statements:
        with pytest.raises(sqlite3.DatabaseError, match="period is locked"):
            scratch_db.execute(sql, params)


def test_a_correction_to_a_closed_month_is_booked_in_the_open_month(scratch_db):
    booked = periods.book_correction(scratch_db, _entry(scratch_db, CLOSED), corrects=CLOSED)
    row = scratch_db.execute("SELECT period, entry_date, corrects_period FROM journal_entries WHERE entry_id = ?",
                             (booked.entry_id,)).fetchone()
    assert tuple(row) == (C.OPEN_MONTH, f"{C.OPEN_MONTH}-30", CLOSED)


@pytest.mark.parametrize("role", ["vouch", "finance_manager", "hr", "treasury", "stock_admin", "department_head"])
def test_only_the_controller_can_reopen(scratch_db, role):
    with pytest.raises(periods.NotAuthorised):
        periods.reopen_period(scratch_db, CLOSED, periods.Actor("someone", role), "please")
    assert periods.status(scratch_db, CLOSED) == "locked"


def test_status_cannot_be_changed_behind_the_controllers_back(scratch_db):
    with pytest.raises(sqlite3.DatabaseError, match="only the controller"):
        scratch_db.execute("UPDATE periods SET status = 'open' WHERE period = ?", (CLOSED,))


def test_controller_reopens_writes_and_relocks_with_a_logged_reason(scratch_db):
    controller = periods.directory_actor(scratch_db, "controller")
    periods.reopen_period(scratch_db, CLOSED, controller, "auditor adjustment")
    periods.post(scratch_db, _entry(scratch_db, CLOSED))
    periods.lock_period(scratch_db, CLOSED, controller, "adjustment booked")
    events = scratch_db.execute("SELECT action, actor, reason FROM period_events WHERE period = ? ORDER BY id",
                                (CLOSED,)).fetchall()
    assert [tuple(e) for e in events][-2:] == [("reopen", "Amara Okafor", "auditor adjustment"),
                                              ("lock", "Amara Okafor", "adjustment booked")]
    with pytest.raises(periods.PeriodLocked):
        periods.post(scratch_db, _entry(scratch_db, CLOSED, "TEST-AFTER-RELOCK"))
