"""C1: segregation of duties, snapshot approvals, locked months and immutable posted entries."""
import sqlite3
from decimal import Decimal as D
import pytest
from vouch.controls import approvals, gates
from vouch.db.periods import person_actor
from test_v3_controls import draft


def test_requester_and_keyer_cannot_approve(controls_db):
    tax=person_actor(controls_db,"tax_manager")
    entry=draft(controls_db); gates.draft_entry(controls_db,gates.VOUCH,entry)
    request=approvals.request(controls_db,tax,"entry",entry.entry_id,{})
    with pytest.raises(gates.Refused,match="requester"):
        approvals.decide(controls_db,tax,request,"approve","My own request")


def test_large_corrections_require_two_independent_people(controls_db):
    entry=draft(controls_db); entry.type="vat_reversal"
    gates.draft_entry(controls_db,gates.VOUCH,entry)
    request=approvals.request(controls_db,gates.VOUCH,"entry",entry.entry_id,{},D("5000"),"EUR")
    assert approvals.get(controls_db,request)["needs"]==["tax_manager","controller"]
    approvals.decide(controls_db,person_actor(controls_db,"tax_manager"),request,"approve","Tax review")
    assert approvals.get(controls_db,request)["status"]=="pending"
    with pytest.raises(gates.Refused):
        gates.post_approved_entry(controls_db,person_actor(controls_db,"controller"),entry.entry_id)
    approvals.decide(controls_db,person_actor(controls_db,"controller"),request,"approve","Controller review")
    gates.post_approved_entry(controls_db,person_actor(controls_db,"controller"),entry.entry_id)
    with pytest.raises(sqlite3.IntegrityError,match="final"):
        controls_db.execute("UPDATE journal_lines SET debit='1.00' WHERE entry_id=?",(entry.entry_id,))


def test_altering_a_draft_invalidates_its_approval(controls_db):
    entry=draft(controls_db); gates.draft_entry(controls_db,gates.VOUCH,entry)
    request=approvals.request(controls_db,gates.VOUCH,"entry",entry.entry_id,{})
    approvals.decide(controls_db,person_actor(controls_db,"tax_manager"),request,"approve","Reviewed")
    with pytest.raises(sqlite3.IntegrityError,match="append-only"):
        controls_db.execute("UPDATE journal_lines SET debit='1.00' WHERE entry_id=? AND line_no=1",(entry.entry_id,))
    controls_db.execute("DROP TRIGGER lines_append_only_update")      # a tamperer who bypasses the database guard
    controls_db.execute("UPDATE journal_lines SET debit='1.00' WHERE entry_id=? AND line_no=1",(entry.entry_id,))
    with pytest.raises(gates.Refused,match="changed"):
        gates.post_approved_entry(controls_db,person_actor(controls_db,"controller"),entry.entry_id)


def test_vouch_cannot_create_an_already_posted_entry(controls_db):
    entry=draft(controls_db);entry.status="posted"
    with pytest.raises(gates.Refused): gates.draft_entry(controls_db,gates.VOUCH,entry)
