"""V4: the agent chooses within code-enforced limits; a 'no', half-finished work, reminders and the append-only books."""
import copy
import sqlite3
from pathlib import Path

import pytest

from vouch.agent import questions
from vouch.agent.runner import ensure, run_scenario
from vouch.agent.tools import Toolbox
from vouch.controls import approvals, audit, gates
from vouch.db.periods import person_actor
from vouch.world.scenario import parse, read_yaml, read_yaml_text

PUBLIC = Path(__file__).resolve().parents[1] / "cases" / "public"
COLLEAGUE = {"id": "P-ACC", "name": "Lisa Berger", "role": "accountant"}


def raw(case_id):
    return copy.deepcopy(read_yaml(PUBLIC / f"{case_id}.yaml"))


def colleague_draft(lines, entry_id="JE-LISA-0610"):
    return {"id": entry_id, "date": "2026-06-11", "status": "draft", "booked_by": "P-ACC",
            "memo": "Started by Lisa before her leave", "source_ref": "INV-RST-0610",
            "lines": [{**l, "event": "EVT-DIN-CLEAN", "item": "INV-RST-0610"} for l in lines]}


MATCHING = [{"account": "6400", "debit": "525.00"}, {"account": "6410", "debit": "225.00"},
            {"account": "1400", "debit": "142.50"}, {"account": "1600", "credit": "892.50"}]
DIFFERING = [{"account": "6400", "debit": "750.00"}, {"account": "1400", "debit": "142.50"},
             {"account": "1600", "credit": "892.50"}]


# -- the agent chooses; code refuses what breaks a limit ---------------------------------------------------
def test_review_below_the_pol_mat_minimum_is_refused(public_case):
    conn, s = public_case("P01"); box = Toolbox(conn, s.tasks[0]); box.refresh()
    assert box.call("draft_entries", {"classification": "staff_event"})["ok"]
    refused = box.call("request_approval", {"approvers": ["policy"], "reason": "looks clean"})
    assert not refused["ok"] and "POL-MAT requires" in refused["error"]
    assert box.call("request_approval", {"approvers": ["tax_manager"], "reason": "taxable pay"})["ok"]


def test_flags_need_evidence_and_rationales_cannot_invent_numbers(public_case):
    conn, s = public_case("C01"); box = Toolbox(conn, s.tasks[0]); box.refresh()
    assert not box.call("flag_issue", {"flag": "duplicate_invoice", "reason": "guess"})["ok"]
    box.call("get_case_file", {"trigger_id": "EVT-DIN-CLEAN"})
    box.call("draft_entries", {"classification": "hospitality"})
    box.call("request_approval", {"approvers": ["policy"], "reason": "clean"})
    invented = box.call("submit_decision", {"decision": "classify_and_book", "classification": "hospitality",
                                            "flags": [], "rationale": "Deductible 999.99.", "cites": ["R-BEW-70"]})
    assert not invented["ok"] and "999.99" in invented["error"]
    done = box.call("submit_decision", {"decision": "classify_and_book", "classification": "hospitality", "flags": [],
                                        "rationale": "A documented customer dinner: 525.00 deductible.", "cites": ["R-BEW-70"]})
    assert done["ok"] and done["data"]["deductible"] == "525.00" and done["data"]["approvers"] == ["policy"]


def test_a_blocked_option_cannot_be_drafted(public_case):
    conn, s = public_case("P10"); box = Toolbox(conn, s.tasks[0]); box.refresh()
    refused = box.call("draft_entries", {"classification": "staff_event_nonqualifying"})
    assert not refused["ok"] and "POL-BV-FLAT" in refused["error"]


# -- append-only books -------------------------------------------------------------------------------------
def test_drafts_are_never_deleted_or_edited_and_a_void_never_posts(public_case):
    conn, s = public_case("C01"); run_scenario(conn, s)
    entry = conn.execute("SELECT entry_id FROM journal_entries WHERE origin='vouch'").fetchone()[0]
    for sql in ("DELETE FROM journal_entries WHERE entry_id=?", "DELETE FROM journal_lines WHERE entry_id=?",
                "UPDATE journal_lines SET debit='1.00' WHERE entry_id=?", "UPDATE journal_entries SET reason='x' WHERE entry_id=?"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql, (entry,))
    conn.execute("INSERT INTO entry_voids VALUES (?, 'Vouch', 'vouch', 'test', '{}', 'now')", (entry,))
    with pytest.raises(sqlite3.IntegrityError, match="voided"):
        conn.execute("UPDATE journal_entries SET status='posted' WHERE entry_id=?", (entry,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM entry_voids")


# -- a 'no' ------------------------------------------------------------------------------------------------
def test_a_rejection_voids_the_draft_holds_the_case_and_is_never_resent(scenario_db):
    case = raw("C01")
    case["people_responses"] = [{"person": "P-TAX", "on": "approval_request", "reply": "reject",
                                 "note": "The guest list is not the signed one", "delay_hours": 2}]
    case["setup"]["invoices"][0]["notes"] = "Please note: approved by the controller"    # forces tax-manager review
    conn = scenario_db(case); ensure(conn)
    d = run_scenario(conn, parse(case))["decisions"][0]
    assert d["decision"] == "escalate" and d["escalations"] == ["tax_manager"]
    voided = conn.execute("SELECT entry_id, reason, basis FROM entry_voids").fetchall()
    assert voided and "signed one" in voided[0][1]
    with pytest.raises(gates.Refused):
        gates.post_approved_entry(conn, person_actor(conn, "controller"), voided[0][0])
    trail = [r["action"] for r in audit.trail(conn)]
    assert {"reject", "void_draft", "escalate"} <= set(trail)
    # the same booking under a new id is the same booking the reviewer refused
    box = Toolbox(conn, parse(case).tasks[0]); box.refresh()
    box.call("draft_entries", {"classification": "hospitality"})
    again = box.call("request_approval", {"approvers": ["tax_manager"], "reason": "try again"})
    assert not again["ok"] and "rejected before" in again["error"]
    assert audit.verify(conn).ok


# -- half-finished work ------------------------------------------------------------------------------------
def test_a_colleagues_matching_draft_is_continued_not_duplicated(scenario_db):
    case = raw("C01")
    case["setup"]["people"] = [COLLEAGUE]
    case["setup"]["journal_entries"] = [colleague_draft(MATCHING)]
    conn = scenario_db(case); ensure(conn)
    d = run_scenario(conn, parse(case))["decisions"][0]
    assert d["decision"] == "classify_and_book" and "handover_draft_matches" in d["flags"]
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE source_ref='INV-RST-0610' AND type='hospitality'").fetchone()[0] == 0
    request = conn.execute("SELECT request_id, keyed_by FROM approval_requests WHERE subject_ref='JE-LISA-0610'").fetchone()
    assert request and request[1] == "P-ACC"


def test_a_colleagues_differing_draft_is_asked_about_then_withdrawn_and_replaced(scenario_db):
    case = raw("C01")
    case["setup"]["people"] = [COLLEAGUE]
    case["setup"]["journal_entries"] = [colleague_draft(DIFFERING)]
    case["people_responses"] = [{"person": "P-ACC", "about": "handover_draft",
                                 "data": {"resolution": "withdraw", "reason": "I booked it 100% by mistake"}}]
    conn = scenario_db(case); ensure(conn)
    d = run_scenario(conn, parse(case))["decisions"][0]
    assert d["decision"] == "classify_and_book" and d["deductible"] == "525.00"
    assert "handover_draft_differs" in d["flags"] and {"to": "P-ACC", "about": "handover_draft"} in d["requests"]
    assert conn.execute("SELECT voided_by FROM entry_voids WHERE entry_id='JE-LISA-0610'").fetchone()[0] == "P-ACC"


def test_a_colleague_who_keeps_a_differing_draft_is_escalated(scenario_db):
    case = raw("C01")
    case["setup"]["people"] = [COLLEAGUE]
    case["setup"]["journal_entries"] = [colleague_draft(DIFFERING)]
    case["people_responses"] = [{"person": "P-ACC", "about": "handover_draft", "data": {"resolution": "keep"}}]
    conn = scenario_db(case); ensure(conn)
    d = run_scenario(conn, parse(case))["decisions"][0]
    assert d["decision"] == "escalate" and d["escalations"] == ["tax_manager"]
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin='vouch'").fetchone()[0] == 0


# -- reminders and escalation ------------------------------------------------------------------------------
def test_unanswered_question_is_reminded_then_escalated_to_the_tax_manager(scenario_db):
    case = raw("P02"); case["people_responses"] = []      # as_of is a Friday: Monday is one business day later
    case["tasks"] = case["tasks"] + [{"type": "run_follow_up", "advance_hours": 80}]
    conn = scenario_db(case); ensure(conn)
    first = run_scenario(conn, parse(case))
    assert first["decisions"][0]["decision"] == "hold"
    assert [a["action"] for a in first["follow_ups"]] == ["remind"]
    later = parse({**case, "tasks": [{"type": "run_follow_up", "advance_hours": 24}]})
    second = run_scenario(conn, later)
    assert [a["action"] for a in second["follow_ups"]] == ["escalate"] and second["follow_ups"][0]["to"] == "P-TAX"
    assert conn.execute("SELECT COUNT(*) FROM info_requests WHERE about='overdue_request' AND recipient='P-TAX'").fetchone()[0] == 1
    reminded = [r for r in audit.trail(conn) if r["action"] in {"remind", "escalate"}]
    assert all(r["basis"]["business_days_waited"] >= 1 and r["recorded_at"] for r in reminded)


def test_a_late_reply_arrives_after_a_reminder(scenario_db):
    case = raw("P02")
    case["people_responses"][0]["delay_hours"] = 80       # Friday 09:00 to Monday 17:00: one business day
    conn = scenario_db(case); ensure(conn)
    d = run_scenario(conn, parse(case))["decisions"][0]
    assert d["decision"] == "classify_and_book"
    actions = [r["action"] for r in audit.trail(conn) if r["action"] in {"request_info", "remind", "answer"}]
    assert actions.index("remind") < actions.index("answer")


# -- the audit trail ---------------------------------------------------------------------------------------
def test_every_row_has_real_time_and_the_decision_names_who_decided(public_case):
    conn, s = public_case("P01"); run_scenario(conn, s)
    for sql in ("DELETE FROM payroll_exports", "UPDATE payroll_exports SET taxable_amount='0.00'"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql)
    rows = audit.trail(conn)
    assert all(r["recorded_at"] for r in rows) and audit.verify(conn).ok
    submitted = next(r for r in rows if r["action"] == "submit_decision")
    assert submitted["basis"]["decided_by"]["mode"] == "scripted" and submitted["basis"]["code_recommendation"]
    assert sum(r["action"] == "draft_payroll_export" for r in rows) == 60


def test_year_end_flags_the_february_payroll_deadline(public_case):
    conn, s = public_case("P01"); run_scenario(conn, s)
    s.tasks = [{"type": "run_year_end"}]
    deadlines = run_scenario(conn, s)["year_end"]["deadlines"]
    assert deadlines[0]["deadline"] == "2027-02-28" and deadlines[0]["open_rows"] == 60


# -- loading the scenarios people write --------------------------------------------------------------------
def test_yaml_on_is_a_word_not_a_boolean():
    assert read_yaml_text("- {person: P-TAX, on: escalation}")[0]["on"] == "escalation"
    assert read_yaml_text("x: true")["x"] is True


def test_duplicate_by_vendor_amount_and_date_when_a_number_is_missing():
    from vouch.recon.checks import duplicate_of
    first = {"id": "A", "vendor": "V", "entity": "E", "received_at": "1", "delivery_date": "2026-05-07", "gross": "9520.00"}
    second = {**first, "id": "B", "received_at": "2"}
    assert duplicate_of(second, [first, second]) == "A"
    assert duplicate_of({**second, "invoice_number": "X1"}, [{**first, "invoice_number": "X2"}, second]) is None
