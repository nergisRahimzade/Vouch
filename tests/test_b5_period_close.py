"""B5: a month closes on the 10th of the next and is locked; something that belongs to a closed month is
booked in the current open period; only the controller can reopen."""
import sqlite3
from decimal import Decimal

import pytest

from vouch.db import periods
from vouch.db.ledger import Entry, Line, SourceRef

CLOSED = "2026-07"          # the base company is as of 2026-12-31: July closed on 2026-08-10


def _entry(period, entry_id="TEST-LATE", day=None):
    src = [SourceRef("rules", "R-BV-ALLOW")]
    return Entry(entry_id, "ENT-DE", period, day or f"{period}-30", "staff_event", "late fireworks invoice",
                 [Line("6440", debit=Decimal("1428.00"), cost_center="CC-ADMIN", event="EVT-SOM26", sources=src),
                  Line("1600", credit=Decimal("1428.00"), sources=src)], "Vouch")


def test_months_past_their_close_date_are_locked_by_the_controller(db):
    locked = [r[0] for r in db.execute("SELECT period FROM periods WHERE status = 'locked' ORDER BY period")]
    assert locked == [f"2026-{m:02d}" for m in range(1, 12)]
    assert [tuple(e) for e in db.execute("SELECT DISTINCT actor, role, action FROM period_events")] == \
        [("Jonas Albrecht", "controller", "lock")]
    assert periods.close_date("2026-09").isoformat() == "2026-10-10"


def test_writing_to_a_locked_month_is_refused(db):
    with pytest.raises(periods.PeriodLocked, match="current open period 2026-12"):
        periods.post(db, _entry(CLOSED))
    assert db.execute("SELECT COUNT(*) FROM journal_entries WHERE entry_id = 'TEST-LATE'").fetchone()[0] == 0


def test_the_database_itself_refuses_locked_writes(db):
    entry_id = "JE-INV-FAS-01"                                # the carnival party booked in February
    statements = [
        ("INSERT INTO journal_entries VALUES ('X', 'ENT-DE', '2026-02', '2026-02-01', 'reclass', 'r', 'EUR', "
         "'posted', 'vouch', 'v', NULL, NULL, NULL, 'now')", ()),
        ("UPDATE journal_lines SET debit = '0.00' WHERE entry_id = ?", (entry_id,)),
        ("DELETE FROM journal_lines WHERE entry_id = ?", (entry_id,)),
        ("DELETE FROM journal_entries WHERE entry_id = ?", (entry_id,)),
        ("UPDATE journal_entries SET period = '2026-12' WHERE entry_id = ?", (entry_id,)),
    ]
    for sql, params in statements:
        with pytest.raises(sqlite3.DatabaseError, match="period is locked"):
            db.execute(sql, params)


def test_something_from_a_closed_month_is_booked_in_the_current_period(db):
    moved = periods.into_open_period(db, _entry(CLOSED))
    assert (moved.period, moved.entry_date, moved.corrects_period) == ("2026-12", "2026-12-31", CLOSED)
    periods.post(db, moved)
    open_entry = _entry("2026-12", "TEST-OPEN", "2026-12-20")
    assert periods.into_open_period(db, open_entry) is open_entry


@pytest.mark.parametrize("role", ["vouch", "tax_manager", "payroll", "cfo", "organizer"])
def test_only_the_controller_can_reopen(db, role):
    with pytest.raises(periods.NotAuthorised):
        periods.reopen_period(db, CLOSED, periods.Actor("someone", role), "please")
    assert periods.status(db, CLOSED) == "locked"


def test_status_cannot_be_changed_behind_the_controllers_back(db):
    with pytest.raises(sqlite3.DatabaseError, match="only the controller"):
        db.execute("UPDATE periods SET status = 'open' WHERE period = ?", (CLOSED,))


def test_controller_reopens_writes_and_relocks_with_a_logged_reason(db):
    controller = periods.person_actor(db, "controller")
    periods.reopen_period(db, CLOSED, controller, "auditor adjustment")
    periods.post(db, _entry(CLOSED))
    periods.lock_period(db, CLOSED, controller, "adjustment booked")
    events = db.execute("SELECT action, actor, reason FROM period_events WHERE period = ? ORDER BY id",
                        (CLOSED,)).fetchall()
    assert [tuple(e) for e in events][-2:] == [("reopen", "Jonas Albrecht", "auditor adjustment"),
                                              ("lock", "Jonas Albrecht", "adjustment booked")]
    with pytest.raises(periods.PeriodLocked):
        periods.post(db, _entry(CLOSED, "TEST-AFTER-RELOCK"))
