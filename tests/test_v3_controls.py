"""Migration coverage for event-tax drafts and approval boundaries; no historical tests altered."""
from datetime import date
from decimal import Decimal as D

import pytest

from vouch.agent.templates import Books, invoice_entry, correction_entry
from vouch.controls import approvals, audit, gates
from vouch.controls.matrix import route
from vouch.db.periods import person_actor
from vouch.recon.checks import duplicate_of


def draft(db):
    from vouch.recon.sources import Sources
    src = Sources(db)
    invoice = next(i for i in src.data["invoices"] if i["matched_event"])
    event = src.events[invoice["matched_event"]]
    on = date.fromisoformat(db.execute("SELECT value FROM scenario WHERE key = 'as_of'").fetchone()[0])
    locked = frozenset(r[0] for r in db.execute("SELECT period FROM periods WHERE status = 'locked'"))
    return invoice_entry(invoice, event,
                         {"classification": "staff_event", "deductible": invoice["net"],
                          "vat_recoverable": invoice["vat"]}, Books(on, locked), entry_id="TEST-EVENT")


def test_drafts_balance_and_late_items_move_to_current_period(db):
    entry = draft(db)
    assert sum(l.debit - l.credit for l in entry.lines) == D(0)
    assert entry.entry_date[:7] == entry.period
    assert entry.corrects_period is not None
    assert all(l.cost_center and l.event and l.sources for l in entry.lines)


def test_approval_required_before_posting_and_audit_verifies(db):
    gates.ensure(db)
    entry = draft(db)
    gates.draft_entry(db, gates.VOUCH, entry)
    with pytest.raises(gates.Refused):
        gates.post_approved_entry(db, person_actor(db, "controller"), entry.entry_id)
    request = approvals.request(db, gates.VOUCH, "entry", entry.entry_id, {"entry": entry.entry_id})
    approvals.decide(db, person_actor(db, "tax_manager"), request, "approve", "Reviewed evidence")
    gates.post_approved_entry(db, person_actor(db, "controller"), entry.entry_id)
    assert db.execute("SELECT status FROM journal_entries WHERE entry_id = ?", (entry.entry_id,)).fetchone()[0] == "posted"
    assert audit.verify(db).ok


@pytest.mark.parametrize("action", ["approve", "post_entry", "reopen_period", "change_election", "file_return", "run_payroll"])
def test_agent_has_no_forbidden_permission(db, action):
    gates.ensure(db)
    with pytest.raises(gates.Refused):
        gates.require(db, gates.VOUCH, action, "target")


def test_payroll_does_not_approve(db):
    gates.ensure(db)
    with pytest.raises(gates.Refused):
        gates.require(db, person_actor(db, "payroll"), "approve", "target")


def test_routing_at_boundaries():
    assert route(nondeductible="999.99")["approvers"] == ["policy"]
    assert route(nondeductible="1000.00")["approvers"] == ["tax_manager"]
    assert route(flags=["third_event_allowance_reassigned"])["approvers"] == ["policy"]
    assert route(wage_taxable="0.01")["approvers"] == ["tax_manager"]
    assert route(corrections=[{"type": "vat_reversal", "amount": "5000.00"}])["approvers"] == ["tax_manager", "controller"]
    assert route(blocked=True, escalations=["tax_manager"])["approvers"] == []


def test_duplicate_normalization_is_vendor_scoped():
    first = {"id": "A", "vendor": "one", "invoice_number": "AB / 123", "received_at": "day-a"}
    later = {"id": "B", "vendor": "one", "invoice_number": "ab-123", "received_at": "day-b"}
    assert duplicate_of(later, [first, later]) == "A"
    assert duplicate_of({**later, "vendor": "two"}, [first, later]) is None
