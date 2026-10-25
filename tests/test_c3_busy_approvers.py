"""C3: waiting never approves; reminders and escalations retain required roles."""
from decimal import Decimal as D
from vouch.controls import approvals,clock,gates
from test_v3_controls import draft


def test_silent_approver_gets_one_reminder_then_escalation(controls_db):
    e=draft(controls_db);e.type="vat_reversal";gates.draft_entry(controls_db,gates.VOUCH,e)
    rid=approvals.request(controls_db,gates.VOUCH,"entry",e.entry_id,{},D("5000"),"EUR")
    clock.advance(controls_db,days=3)
    assert approvals.follow_up(controls_db,gates.VOUCH)==[{"request_id":rid,"action":"reminded"}]
    clock.advance(controls_db, days=1)  # Sunday -> Monday: the second weekday interval has elapsed.
    assert approvals.follow_up(controls_db,gates.VOUCH)==[{"request_id":rid,"action":"escalated"}]
    assert approvals.follow_up(controls_db,gates.VOUCH)==[]
    item=approvals.get(controls_db,rid)
    assert item["needs"]==["tax_manager","controller"] and item["status"]=="escalated"
    assert not item["approvals"]
    assert controls_db.execute("SELECT status FROM journal_entries WHERE entry_id=?",(e.entry_id,)).fetchone()[0]=="draft"
