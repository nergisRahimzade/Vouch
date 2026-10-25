"""C2: inbox ownership, decision notes, duplicate requests and rejections."""
import pytest
from vouch.controls import approvals,gates
from vouch.db.periods import person_actor
from test_v3_controls import draft


def test_inbox_routes_to_tax_manager_and_requests_are_idempotent(controls_db):
    e=draft(controls_db);gates.draft_entry(controls_db,gates.VOUCH,e)
    rid=approvals.request(controls_db,gates.VOUCH,"entry",e.entry_id,{})
    assert approvals.request(controls_db,gates.VOUCH,"entry",e.entry_id,{})==rid
    assert approvals.inbox(controls_db,"tax_manager")[0]["request_id"]==rid
    assert approvals.inbox(controls_db,"payroll")==[]
    with pytest.raises(gates.Refused): approvals.decide(controls_db,person_actor(controls_db,"controller"),rid,"approve","Wrong role")
    with pytest.raises(gates.Refused): approvals.decide(controls_db,person_actor(controls_db,"tax_manager"),rid,"approve","")


def test_rejection_is_recorded_and_identical_request_is_refused(controls_db):
    e=draft(controls_db);gates.draft_entry(controls_db,gates.VOUCH,e)
    rid=approvals.request(controls_db,gates.VOUCH,"entry",e.entry_id,{})
    approvals.decide(controls_db,person_actor(controls_db,"tax_manager"),rid,"reject","Need more evidence")
    assert controls_db.execute("SELECT note FROM approval_rejections").fetchone()[0]=="Need more evidence"
    with pytest.raises(gates.Refused,match="rejected"):
        approvals.request(controls_db,gates.VOUCH,"entry",e.entry_id,{})
