"""C1: every write checks the actor's role; one test per rule shows the agent refused."""
import json
import sqlite3
from decimal import Decimal as D

import pytest

from vouch.controls import approvals, gates
from vouch.controls.gates import VOUCH, Refused
from vouch.db import periods
from vouch.db.ledger import Entry, Line, SourceRef
from vouch.world import config as C

OPEN = C.OPEN_MONTH


def actor(conn, role, department=None):
    people = json.loads(conn.execute("SELECT content FROM documents WHERE kind = 'directory'").fetchone()[0])["people"]
    person = next(p for p in people if p["role"] == role and (department is None or p.get("department") == department))
    return periods.Actor(person["name"], role)


def evidence(conn, kind="payroll_register", month=OPEN):
    doc, row = conn.execute("SELECT r.doc_id, r.row_no FROM source_rows r JOIN documents d USING (doc_id) "
                            "WHERE d.kind = ? AND d.month = ? LIMIT 1", (kind, month)).fetchone()
    return [SourceRef(doc, row)]


def entry(conn, entry_id="DRAFT-1", period=OPEN, amount="100.00", account="6000", sources=None):
    sources = sources or evidence(conn)
    return Entry(entry_id, "US", period, f"{period}-28", "test accrual",
                 [Line(account, debit=D(amount), department="Engineering", sources=sources),
                  Line("2100" if account == "6000" else "3100", credit=D(amount), sources=sources)], "Vouch")


def refused_logged(conn, rule):
    row = conn.execute("SELECT outcome, basis FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    return row["outcome"] == "refused" and json.loads(row["basis"])["rule"] == rule


def test_vouch_cannot_approve_post_reopen_or_move_money(controls_db):
    gates.draft_entry(controls_db, VOUCH, entry(controls_db))
    request = approvals.request(controls_db, VOUCH, "entry", "DRAFT-1", {"entry": "DRAFT-1"}, D("100.00"), "USD")
    for attempt in (lambda: approvals.decide(controls_db, VOUCH, request, "approve", "looks fine"),
                    lambda: gates.post_approved_entry(controls_db, VOUCH, "DRAFT-1"),
                    lambda: gates.reopen_period(controls_db, VOUCH, "2026-05", "fix"),
                    lambda: gates.release_payment(controls_db, VOUCH, "any-payment")):
        with pytest.raises(Refused) as refusal:
            attempt()
        assert refusal.value.rule == "ROLE" and refused_logged(controls_db, "ROLE")


def test_the_requester_cannot_approve(controls_db):
    head = actor(controls_db, "department_head", "GTM")
    request = approvals.request(controls_db, head, "accrual", "bonus-accrual", {"amount": "250000"}, D("250000"), "USD")
    with pytest.raises(Refused, match="requester") as refusal:
        approvals.decide(controls_db, head, request, "approve", "mine")
    assert refusal.value.rule == "FIN-03" and refused_logged(controls_db, "FIN-03")


def test_whoever_keyed_a_change_cannot_approve_it(controls_db):
    manager = actor(controls_db, "finance_manager")
    gates.draft_entry(controls_db, VOUCH, entry(controls_db))
    request = approvals.request(controls_db, VOUCH, "entry", "DRAFT-1", {"entry": "DRAFT-1"}, D("100.00"), "USD",
                                keyed_by=manager.name)
    with pytest.raises(Refused, match="keyed") as refusal:
        approvals.decide(controls_db, manager, request, "approve", "ok")
    assert refusal.value.rule == "FIN-03"


def test_large_entries_need_two_approvers_including_the_controller(controls_db):
    manager, controller = actor(controls_db, "finance_manager"), actor(controls_db, "controller")
    gates.draft_entry(controls_db, VOUCH, entry(controls_db, amount="60000.00"))
    request = approvals.request(controls_db, VOUCH, "entry", "DRAFT-1", {"entry": "DRAFT-1"}, D("60000.00"), "USD")
    assert approvals.get(controls_db, request)["needs"] == ["finance_manager", "controller"]
    approvals.decide(controls_db, manager, request, "approve", "checked against the register")
    with pytest.raises(Refused) as refusal:
        gates.post_approved_entry(controls_db, controller, "DRAFT-1")      # only one of two approvals so far
    assert refusal.value.rule == "FIN-04"
    with pytest.raises(Refused, match="two different people"):
        approvals.decide(controls_db, manager, request, "approve", "again")
    approvals.decide(controls_db, controller, request, "approve", "second approval")
    gates.post_approved_entry(controls_db, controller, "DRAFT-1")
    status = controls_db.execute("SELECT status FROM journal_entries WHERE entry_id = 'DRAFT-1'").fetchone()[0]
    assert status == "posted"
    assert approvals.needs_for(controls_db, "entry", D("49999.99"), "USD") == ["finance_manager"]


def test_a_draft_cannot_be_posted_behind_the_gates(controls_db):
    gates.draft_entry(controls_db, VOUCH, entry(controls_db))
    with pytest.raises(sqlite3.DatabaseError, match="approval request"):
        controls_db.execute("UPDATE journal_entries SET status = 'posted' WHERE entry_id = 'DRAFT-1'")


def test_closed_months_are_locked(controls_db):
    closed = entry(controls_db, period="2026-05", sources=evidence(controls_db, month="2026-05"))
    with pytest.raises(Refused, match="locked") as refusal:
        gates.draft_entry(controls_db, VOUCH, closed)
    assert refusal.value.rule == "FIN-05" and refused_logged(controls_db, "FIN-05")


def test_bank_changes_need_hr_and_a_call_back(controls_db):
    hr, treasury = actor(controls_db, "hr"), actor(controls_db, "treasury")
    first = approvals.request(controls_db, VOUCH, "bank_change", "employee-x", {"new_account": "A"})
    approvals.decide(controls_db, hr, first, "approve", "looks right")              # no call-back
    with pytest.raises(Refused, match="call-back") as refusal:
        gates.apply_bank_change(controls_db, hr, first)
    assert refusal.value.rule == "PAY-06"
    with pytest.raises(Refused):
        gates.apply_bank_change(controls_db, VOUCH, first)
    second = approvals.request(controls_db, VOUCH, "bank_change", "employee-y", {"new_account": "B"})
    approvals.decide(controls_db, hr, second, "approve", "called the number on file", callback_confirmed=True)
    gates.apply_bank_change(controls_db, hr, second)
    third = approvals.request(controls_db, VOUCH, "bank_change", "employee-z", {"new_account": "C"})
    with pytest.raises(Refused):
        gates.apply_bank_change(controls_db, treasury, third)


def test_no_expense_for_an_unapproved_grant(controls_db):
    pending = controls_db.execute("SELECT doc_id, row_no FROM v_equity_report WHERE month = ? AND status = "
                                  "'pending_approval' LIMIT 1", (OPEN,)).fetchone()
    with pytest.raises(Refused, match="board approval") as refusal:
        gates.draft_entry(controls_db, VOUCH, entry(controls_db, account="6300", sources=[SourceRef(*pending)]))
    assert refusal.value.rule == "FIN-07"


def test_duplicates_are_refused(controls_db):
    treasury = actor(controls_db, "treasury")
    gates.draft_entry(controls_db, VOUCH, entry(controls_db, "DRAFT-1"))
    with pytest.raises(Refused, match="repeats") as refusal:
        gates.draft_entry(controls_db, VOUCH, entry(controls_db, "DRAFT-2"))
    assert refusal.value.rule == "FIN-08"
    gates.release_payment(controls_db, treasury, "payment-1")
    with pytest.raises(Refused, match="already released"):
        gates.release_payment(controls_db, treasury, "payment-1")


def test_a_held_payment_cannot_be_released(controls_db):
    treasury, hr = actor(controls_db, "treasury"), actor(controls_db, "hr")
    hold = gates.place_hold(controls_db, VOUCH, "case-1", "payment", "payment-2", "bank details differ from HR")
    with pytest.raises(Refused, match="held") as refusal:
        gates.release_payment(controls_db, treasury, "payment-2")
    assert refusal.value.rule == "PAY-06"
    with pytest.raises(Refused):
        gates.release_hold(controls_db, VOUCH, hold, "my own hold")
    gates.release_hold(controls_db, hr, hold, "called back, confirmed")
    gates.release_payment(controls_db, treasury, "payment-2")
