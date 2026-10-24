"""C2: requests wait for the right role; approve or reject with a note; a rejection becomes a
precedent the agent must respect; a rejected request is never resent unchanged."""
from decimal import Decimal as D

import pytest

from vouch.controls import approvals
from vouch.controls.gates import VOUCH, Refused
from vouch.recon.run import reconcile, stored
from test_c1_gates import actor


def test_requests_wait_in_the_right_inbox(controls_db):
    hr, treasury = actor(controls_db, "hr"), actor(controls_db, "treasury")
    bank = approvals.request(controls_db, VOUCH, "bank_change", "employee-x", {"new_account": "A"})
    funding = approvals.request(controls_db, VOUCH, "funding", "batch-x", {"payments": ["p1", "p2"]}, D("10.00"), "GBP")
    assert [r["request_id"] for r in approvals.inbox(controls_db, "hr")] == [bank]
    assert [r["request_id"] for r in approvals.inbox(controls_db, "treasury")] == [funding]
    with pytest.raises(Refused, match="needs the hr"):
        approvals.decide(controls_db, treasury, bank, "approve", "not mine to decide")
    approvals.decide(controls_db, hr, bank, "approve", "called back", callback_confirmed=True)
    assert approvals.inbox(controls_db, "hr") == []


def test_a_decision_needs_a_note(controls_db):
    hr = actor(controls_db, "hr")
    bank = approvals.request(controls_db, VOUCH, "bank_change", "employee-x", {"new_account": "A"})
    with pytest.raises(Refused, match="note"):
        approvals.decide(controls_db, hr, bank, "reject", "  ")


def test_a_rejected_request_is_never_resent_unchanged(controls_db):
    hr = actor(controls_db, "hr")
    first = approvals.request(controls_db, VOUCH, "bank_change", "employee-x", {"new_account": "A"})
    approvals.decide(controls_db, hr, first, "reject", "employee says they never asked for this")
    with pytest.raises(Refused, match="rejected before") as refusal:
        approvals.request(controls_db, VOUCH, "bank_change", "employee-x", {"new_account": "A"})
    assert refusal.value.rule == "PRECEDENT"
    changed = approvals.request(controls_db, VOUCH, "bank_change", "employee-x",
                                {"new_account": "A", "evidence": "call-back booked with the employee"})
    assert changed != first


def test_a_rejection_becomes_a_precedent_later_closes_cite(controls_db):
    reconcile(controls_db)
    case = next(f for f in stored(controls_db) if f["disposition"] == "case")
    controller = actor(controls_db, "controller")
    request = approvals.request(controls_db, VOUCH, "accrual", case["finding_id"],
                                {"case_kind": case["kind"], "proposal": "release"})
    approvals.decide(controls_db, controller, request, "reject", "keep it held until the source is confirmed")
    controls_db.commit()
    reconcile(controls_db)
    again = next(f for f in stored(controls_db) if f["finding_id"] == case["finding_id"])
    assert any(p["ruling_id"] == f"RUL-{request}" and p["decision"] == "rejected" for p in again["precedents"])
