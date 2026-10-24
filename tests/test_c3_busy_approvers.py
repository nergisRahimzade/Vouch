"""C3: an unanswered request stays held, gets a reminder, then escalates to the controller
(simulated clock); each step is logged."""
from decimal import Decimal as D

import pytest

from vouch.controls import approvals, clock, gates
from vouch.controls.gates import VOUCH, Refused
from test_c1_gates import actor, entry


def _audit(conn, action):
    return conn.execute("SELECT reason FROM audit_log WHERE action = ? ORDER BY seq", (action,)).fetchall()


def test_silent_approver_gets_a_reminder_then_escalation(controls_db):
    hr, controller = actor(controls_db, "hr"), actor(controls_db, "controller")
    request = approvals.request(controls_db, VOUCH, "bank_change", "employee-x", {"new_account": "A"})
    clock.advance(controls_db, days=1)
    assert approvals.follow_up(controls_db, VOUCH) == []          # one business day: nothing yet
    while clock.business_days_between(approvals.get(controls_db, request)["created_at"], clock.now(controls_db)) < 2:
        clock.advance(controls_db, days=1)
    assert approvals.follow_up(controls_db, VOUCH) == [{"request_id": request, "action": "reminded"}]
    assert _audit(controls_db, "remind") and approvals.get(controls_db, request)["status"] == "pending"
    while clock.business_days_between(approvals.get(controls_db, request)["created_at"], clock.now(controls_db)) < 4:
        clock.advance(controls_db, days=1)
    assert approvals.follow_up(controls_db, VOUCH) == [{"request_id": request, "action": "escalated"}]
    item = approvals.get(controls_db, request)
    assert item["status"] == "escalated" and item["assigned_role"] == "controller" and _audit(controls_db, "escalate")
    with pytest.raises(Refused):
        approvals.decide(controls_db, hr, request, "approve", "late answer")      # now the controller's call
    approvals.decide(controls_db, controller, request, "reject", "no call-back possible; keep the old account")
    assert approvals.get(controls_db, request)["status"] == "rejected"


def test_waiting_never_approves_or_releases_anything(controls_db):
    treasury = actor(controls_db, "treasury")
    gates.place_hold(controls_db, VOUCH, "case-1", "payment", "payment-9", "awaiting HR call-back")
    approvals.request(controls_db, VOUCH, "payment_release", "payment-9", {"payment": "payment-9"})
    clock.advance(controls_db, days=30)
    approvals.follow_up(controls_db, VOUCH)
    with pytest.raises(Refused, match="held"):
        gates.release_payment(controls_db, treasury, "payment-9")


def test_escalating_a_large_entry_keeps_two_different_approvers(controls_db):
    manager, controller = actor(controls_db, "finance_manager"), actor(controls_db, "controller")
    gates.draft_entry(controls_db, VOUCH, entry(controls_db, amount="80000.00"))
    request = approvals.request(controls_db, VOUCH, "entry", "DRAFT-1", {"entry": "DRAFT-1"}, D("80000.00"), "USD")
    clock.advance(controls_db, days=10)
    approvals.follow_up(controls_db, VOUCH)
    assert approvals.get(controls_db, request)["needs"] == ["controller", "finance_manager"]
    approvals.decide(controls_db, controller, request, "approve", "escalated to me; approved")
    approvals.decide(controls_db, manager, request, "approve", "second approver")
    gates.post_approved_entry(controls_db, controller, "DRAFT-1")
