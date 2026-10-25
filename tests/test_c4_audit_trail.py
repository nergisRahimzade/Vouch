"""C4: append-only hash chain detects content mutation and row deletion."""
import sqlite3
import pytest
from vouch.controls import audit,gates


def test_trail_records_who_what_why_and_refuses_edits(controls_db):
    audit.log(controls_db,gates.VOUCH,"test","item","done","Evidence checked",{"rule":"R-BV-COST v1"})
    row=audit.trail(controls_db)[-1]
    assert row["actor"]=="Vouch" and row["basis"]["rule"]=="R-BV-COST v1"
    assert audit.verify(controls_db).ok
    with pytest.raises(sqlite3.IntegrityError): controls_db.execute("UPDATE audit_log SET reason='tampered'")
    with pytest.raises(sqlite3.IntegrityError): controls_db.execute("DELETE FROM audit_log")


@pytest.mark.parametrize("column,value",[("reason","wrong"),("actor","wrong"),("prev_hash","bad")])
def test_changed_content_breaks_the_hash_chain(controls_db,column,value):
    audit.log(controls_db,gates.VOUCH,"test","item","done","Original",{})
    controls_db.execute("DROP TRIGGER audit_no_update")
    controls_db.execute(f"UPDATE audit_log SET {column}=?",(value,))
    assert not audit.verify(controls_db).ok


def test_deleting_middle_row_breaks_verification(controls_db):
    for n in range(3): audit.log(controls_db,gates.VOUCH,"test",str(n),"done","Recorded",{})
    controls_db.execute("DROP TRIGGER audit_no_delete")
    controls_db.execute("DELETE FROM audit_log WHERE seq=2")
    assert not audit.verify(controls_db).ok
