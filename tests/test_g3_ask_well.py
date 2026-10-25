"""G3: each question names the person, the fact needed, the sources that disagree and what happens on
yes, no or a partial answer; yes, no and partial each lead to the right next step; unanswered stays held."""
import pytest

from vouch.agent import questions, templates
from vouch.agent.runner import ensure
from vouch.agent.tools import Toolbox
from vouch.controls import clock, gates
from vouch.controls.gates import VOUCH, Refused
from vouch.recon.run import reconcile
from vouch.world import config as C
from test_c1_gates import actor

SOURCES = ["months/2026-09/payment_file_DE.csv", "months/2026-09/hr_census.csv"]


@pytest.fixture
def held_case(controls_db):
    """A bank-change case with the payment held and HR asked, as the agent leaves it."""
    ensure(controls_db)
    findings = reconcile(controls_db)
    case = next(f for f in findings.cases() if f.kind == "bank_details_differ_from_hr")
    gates.open_case(controls_db, VOUCH, case.finding_id, C.OPEN_MONTH, case.kind, case.summary)
    toolbox = Toolbox(controls_db)
    payment = case.subject["payment_id"]
    toolbox.call("hold", {"case_id": case.finding_id, "target_kind": "payment", "target_ref": payment,
                          "reason": case.summary})
    asked = toolbox.call("ask", {"case_id": case.finding_id, "to_role": "hr",
                                 "fact_needed": "Did a call-back to the number on file confirm the new account?",
                                 "sources_disagree": SOURCES, "if_yes": "release_hold", "if_no": "keep_hold",
                                 "if_partial": "ask_follow_up"})
    return controls_db, case.finding_id, payment, asked["question_id"], toolbox


def status(conn, case_id):
    return conn.execute("SELECT status FROM cases WHERE case_id = ?", (case_id,)).fetchone()[0]


def test_a_question_names_the_person_fact_sources_and_each_next_step(held_case):
    conn, case_id, _, question_id, _ = held_case
    q = questions.get(conn, question_id)
    assert q["to_person"] == actor(conn, "hr").name and q["fact_needed"] and len(q["sources"]) >= 2
    assert (q["if_yes"], q["if_no"], q["if_partial"]) == ("release_hold", "keep_hold", "ask_follow_up")
    with pytest.raises(questions.BadQuestion, match="two sources"):
        questions.ask(conn, case_id, "hr", q["to_person"], "anything", SOURCES[:1], "keep_hold", "keep_hold", "keep_hold")
    with pytest.raises(questions.BadQuestion, match="next step"):
        questions.ask(conn, case_id, "hr", q["to_person"], "anything", SOURCES, "approve_it", "keep_hold", "keep_hold")
    with pytest.raises(questions.BadQuestion, match="fact needed"):
        questions.ask(conn, case_id, "hr", q["to_person"], " ", SOURCES, "keep_hold", "keep_hold", "keep_hold")


def test_yes_releases_the_hold_through_the_person_who_answered(held_case):
    conn, case_id, payment, question_id, _ = held_case
    hr, treasury = actor(conn, "hr"), actor(conn, "treasury")
    result = questions.answer(conn, question_id, hr, "yes", "called her on the number on file; she confirmed")
    assert result["next_step"] == "release_hold" and status(conn, case_id) == "resolved"
    assert gates.active_holds(conn, "payment", payment) == []
    released_by = conn.execute("SELECT released_by FROM holds WHERE target_ref = ?", (payment,)).fetchone()[0]
    assert released_by == hr.name
    gates.release_payment(conn, treasury, payment)


def test_no_keeps_the_hold_and_the_money_where_it_is(held_case):
    conn, case_id, payment, question_id, _ = held_case
    hr, treasury = actor(conn, "hr"), actor(conn, "treasury")
    result = questions.answer(conn, question_id, hr, "no", "she never asked for a change; the email is not hers")
    assert result["next_step"] == "keep_hold" and status(conn, case_id) == "held"
    with pytest.raises(Refused, match="held"):
        gates.release_payment(conn, treasury, payment)


def test_a_partial_answer_stays_held_and_asks_for_the_rest(held_case):
    conn, case_id, payment, question_id, _ = held_case
    hr = actor(conn, "hr")
    result = questions.answer(conn, question_id, hr, "partial", "left a voicemail; no answer yet")
    assert result["next_step"] == "ask_follow_up" and status(conn, case_id) == "held"
    follow = questions.open_questions(conn, case_id)
    assert len(follow) == 1 and follow[0]["follows"] == question_id and "voicemail" in follow[0]["fact_needed"]
    assert gates.active_holds(conn, "payment", payment)


def test_draft_correction_on_yes_drafts_and_requests_approval(controls_db):
    ensure(controls_db)
    findings = reconcile(controls_db)
    case = next(f for f in findings.cases() if f.kind == "contribution_rate_differs_from_policy")
    gates.open_case(controls_db, VOUCH, case.finding_id, C.OPEN_MONTH, case.kind, case.summary)
    toolbox = Toolbox(controls_db)
    asked = toolbox.call("ask", {"case_id": case.finding_id, "to_role": "finance_manager",
                                 "fact_needed": "Is 5% the employer pension rate we owe for this month?",
                                 "sources_disagree": ["payroll_register_UK", "pay_policy"],
                                 "if_yes": "draft_correction", "if_no": "escalate_to_controller",
                                 "if_partial": "ask_follow_up"})

    def draft(case_id):
        entry = toolbox.draft_for(case_id)
        toolbox.call("request_approval", {"case_id": case_id, "kind": "entry", "subject_ref": entry, "note": "pension"})
        return entry

    questions.answer(controls_db, asked["question_id"], actor(controls_db, "finance_manager"), "yes",
                     "policy rate confirmed", draft=draft)
    assert controls_db.execute("SELECT status FROM journal_entries WHERE entry_id = ?",
                               (f"VCH-{case.finding_id}",)).fetchone()[0] == "draft"
    assert controls_db.execute("SELECT status FROM approval_requests WHERE subject_ref = ?",
                               (f"VCH-{case.finding_id}",)).fetchone()[0] == "pending"


def test_only_the_person_asked_answers_once(held_case):
    conn, _, _, question_id, _ = held_case
    with pytest.raises(Refused):
        questions.answer(conn, question_id, actor(conn, "treasury"), "yes", "not mine")
    questions.answer(conn, question_id, actor(conn, "hr"), "no", "no change requested")
    with pytest.raises(Refused, match="already answered"):
        questions.answer(conn, question_id, actor(conn, "hr"), "yes", "changed my mind")


def test_an_unanswered_question_stays_held_is_reminded_then_escalated(held_case):
    conn, case_id, payment, question_id, _ = held_case
    actions = []
    for _ in range(8):
        clock.advance(conn, days=1)
        actions += [a["action"] for a in questions.follow_up(conn)]
    assert actions == ["reminded", "escalated"]
    assert questions.get(conn, question_id)["to_role"] == "controller"
    assert status(conn, case_id) in ("held", "waiting") and gates.active_holds(conn, "payment", payment)


def test_a_question_cannot_promise_a_correction_for_a_case_that_takes_no_entry(controls_db):
    ensure(controls_db)
    for f in reconcile(controls_db).cases():
        gates.open_case(controls_db, VOUCH, f.finding_id, C.OPEN_MONTH, f.kind, f.summary)
    toolbox = Toolbox(controls_db)
    no_entry = [c for c in toolbox.cases.values() if templates.why_no_correction(toolbox.src, c)]
    takes_entry = [c for c in toolbox.cases.values() if not templates.why_no_correction(toolbox.src, c)]
    assert no_entry and takes_entry
    for case in no_entry:
        result = toolbox.call("ask", {"case_id": case["finding_id"], "to_role": "controller",
                                      "fact_needed": "Should this be corrected?", "sources_disagree": SOURCES,
                                      "if_yes": "draft_correction", "if_no": "keep_hold", "if_partial": "keep_hold"})
        assert result["ok"] is False and "draft_correction is not possible" in result["error"]
    asked = {r[0] for r in controls_db.execute("SELECT case_id FROM questions")}
    assert asked.isdisjoint(c["finding_id"] for c in no_entry)


def test_a_promised_correction_that_cannot_be_drafted_holds_the_case_with_a_reason(controls_db):
    ensure(controls_db)
    for f in reconcile(controls_db).cases():
        gates.open_case(controls_db, VOUCH, f.finding_id, C.OPEN_MONTH, f.kind, f.summary)
    toolbox = Toolbox(controls_db)
    case = next(c for c in toolbox.cases.values() if templates.why_no_correction(toolbox.src, c))
    # asked before the case changed: the promise was possible then, not now
    question_id = questions.ask(controls_db, case["finding_id"], "controller", "Amara Okafor", "Correct this?",
                                SOURCES, "draft_correction", "keep_hold", "keep_hold")
    result = questions.answer(controls_db, question_id, actor(controls_db, "controller"), "yes", "go ahead",
                              draft=toolbox.draft_for)
    assert result["failed"]
    status, note = controls_db.execute("SELECT status, note FROM cases WHERE case_id = ?",
                                       (case["finding_id"],)).fetchone()
    assert status == "held" and "could not be drafted" in note
    assert not controls_db.execute("SELECT 1 FROM journal_entries WHERE entry_id = ?",
                                   (f"VCH-{case['finding_id']}",)).fetchone()
