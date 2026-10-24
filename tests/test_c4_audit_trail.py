"""C4: append-only, hash-chained audit trail; editing any past row makes verify fail."""
import sqlite3
import subprocess
import sys
from decimal import Decimal as D

import pytest

from vouch.controls import approvals, audit, gates
from vouch.controls.gates import VOUCH, Refused
from test_c1_gates import actor, entry


def _activity(conn):
    manager = actor(conn, "finance_manager")
    gates.open_case(conn, VOUCH, "case-1", "2026-09", "test", "a test case")
    gates.place_hold(conn, VOUCH, "case-1", "payment", "payment-1", "bank details differ from HR", {"rule": "PAY-06"})
    gates.draft_entry(conn, VOUCH, entry(conn))
    request = approvals.request(conn, VOUCH, "entry", "DRAFT-1", {"entry": "DRAFT-1"}, D("100.00"), "USD")
    approvals.decide(conn, manager, request, "approve", "agrees with the register")
    with pytest.raises(Refused):
        approvals.decide(conn, VOUCH, request, "approve", "self")
    conn.commit()


def test_every_row_records_who_what_when_why_and_basis(controls_db):
    _activity(controls_db)
    rows = audit.trail(controls_db)
    assert len(rows) >= 6
    for row in rows:
        assert row["actor"] and row["role"] and row["action"] and row["at"] and row["reason"] and row["outcome"]
        assert isinstance(row["basis"], dict)
    assert {"done", "requested", "refused"} <= {r["outcome"] for r in rows}
    assert audit.verify(controls_db).ok


def test_the_trail_refuses_edits_and_deletes(controls_db):
    _activity(controls_db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        controls_db.execute("UPDATE audit_log SET reason = 'nothing to see' WHERE seq = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        controls_db.execute("DELETE FROM audit_log WHERE seq = 2")


@pytest.mark.parametrize("column, value", [("reason", "edited"), ("actor", "Someone Else"), ("at", "2000-01-01T00:00:00"),
                                           ("basis", '{"rule": "none"}'), ("outcome", "done")])
def test_editing_any_past_row_breaks_verification(controls_db, column, value):
    _activity(controls_db)
    rows = audit.trail(controls_db)
    target = rows[len(rows) // 2]["seq"]
    controls_db.execute("DROP TRIGGER audit_no_update")          # someone with file access bypasses the trigger
    controls_db.execute(f"UPDATE audit_log SET {column} = ? WHERE seq = ?", (value, target))
    result = audit.verify(controls_db)
    assert not result.ok and result.first_bad_seq == target


def test_deleting_a_row_breaks_verification(controls_db):
    _activity(controls_db)
    controls_db.execute("DROP TRIGGER audit_no_delete")
    controls_db.execute("DELETE FROM audit_log WHERE seq = 3")
    result = audit.verify(controls_db)
    assert not result.ok and result.first_bad_seq == 4


def test_verify_command_fails_on_a_tampered_trail(controls_db, tmp_path):
    _activity(controls_db)
    path = controls_db.execute("PRAGMA database_list").fetchone()[2]
    ok = subprocess.run([sys.executable, "-m", "vouch.controls", "--db", path, "verify"], capture_output=True, text=True)
    assert ok.returncode == 0 and "intact" in ok.stdout
    controls_db.execute("DROP TRIGGER audit_no_update")
    controls_db.execute("UPDATE audit_log SET reason = 'edited' WHERE seq = 1")
    controls_db.commit()
    broken = subprocess.run([sys.executable, "-m", "vouch.controls", "--db", path, "verify"], capture_output=True, text=True)
    assert broken.returncode == 1 and "BROKEN" in broken.stdout and "first bad row 1" in broken.stdout
