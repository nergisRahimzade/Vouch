"""Questions to colleagues. Each names the person, the fact needed, the sources that disagree,
and what happens on yes, no or a partial answer. Answers are handled in code: the case moves on
when answered and stays held when not."""
from __future__ import annotations

import json
import sqlite3

from vouch.agent.untrusted import instruction_flags
from vouch.controls import approvals, audit, clock, gates
from vouch.controls.gates import VOUCH, Refused
from vouch.db.periods import Actor

# The next steps a question may promise. Releasing a hold is done by the person who answered,
# never by Vouch.
NEXT_STEPS = {
    "release_hold": "the person answering releases the hold and the case is resolved",
    "keep_hold": "the hold stays; the case stays held",
    "draft_correction": "Vouch drafts the correcting entry and requests approval",
    "resolve_case": "the case is resolved with the answer recorded",
    "ask_follow_up": "Vouch asks a follow-up question for what is still missing",
    "escalate_to_controller": "the case goes to the controller",
}


class BadQuestion(ValueError):
    pass


def ask(conn: sqlite3.Connection, case_id: str, to_role: str, to_person: str, fact_needed: str,
        sources: list[str], if_yes: str, if_no: str, if_partial: str, follows: str | None = None) -> str:
    gates.require(conn, VOUCH, "ask_question", case_id)
    missing = [name for name, value in (("person", to_person), ("fact needed", fact_needed)) if not str(value).strip()]
    if len(sources) < 2:
        missing.append("at least two sources that disagree")
    for label, step in (("yes", if_yes), ("no", if_no), ("partial", if_partial)):
        if step not in NEXT_STEPS:
            missing.append(f"a known next step for {label} (got {step!r})")
        elif step == "release_hold" and "release_hold" not in gates.PERMISSIONS.get(to_role, set()):
            missing.append(f"a next step the {to_role} can take for {label} (the {to_role} cannot release holds; "
                           "use escalate_to_controller)")
    if missing:
        raise BadQuestion("a question must name " + ", ".join(missing))
    duplicate = conn.execute("SELECT question_id FROM questions WHERE case_id = ? AND to_role = ? AND fact_needed = ? "
                             "AND status = 'open'", (case_id, to_role, fact_needed)).fetchone()
    if duplicate:
        return duplicate[0]
    question_id = f"Q-{conn.execute('SELECT COUNT(*) FROM questions').fetchone()[0] + 1:04d}"
    conn.execute("INSERT INTO questions (question_id, case_id, to_role, to_person, fact_needed, sources, if_yes, if_no, "
                 "if_partial, status, asked_at, follows) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                 (question_id, case_id, to_role, to_person, fact_needed, json.dumps(sources), if_yes, if_no,
                  if_partial, clock.now(conn), follows))
    audit.log(conn, VOUCH, "ask", case_id, "requested", fact_needed,
              {"question": question_id, "to": to_person, "role": to_role, "sources": sources,
               "if_yes": if_yes, "if_no": if_no, "if_partial": if_partial})
    gates.set_case(conn, VOUCH, case_id, "waiting", f"asked {to_person}: {fact_needed}", {"question": question_id})
    return question_id


def get(conn, question_id: str) -> dict:
    row = conn.execute("SELECT * FROM questions WHERE question_id = ?", (question_id,)).fetchone()
    return {**dict(row), "sources": json.loads(row["sources"])}


def open_questions(conn, case_id: str | None = None) -> list[dict]:
    query = "SELECT question_id FROM questions WHERE status = 'open'" + (" AND case_id = ?" if case_id else "")
    return [get(conn, r[0]) for r in conn.execute(query, (case_id,) if case_id else ()).fetchall()]


def answer(conn: sqlite3.Connection, question_id: str, by: Actor, reply: str, note: str,
           draft=None) -> dict:
    """Record an answer and carry out the step the question promised for it.
    `draft` is a callable(case_id) that drafts and requests approval for a correction."""
    question = get(conn, question_id)
    if question["status"] != "open":
        raise Refused("FIN-08", "this question was already answered")
    if by.role != question["to_role"] and by.role != "controller":
        raise Refused("ROLE", f"the question was asked of the {question['to_role']}, not the {by.role}")
    if reply not in ("yes", "no", "partial"):
        raise ValueError(f"an answer is yes, no or partial, not {reply!r}")
    conn.execute("UPDATE questions SET status = 'answered', answer = ?, answered_by = ?, note = ?, answered_at = ? "
                 "WHERE question_id = ?", (reply, by.name, note, clock.now(conn), question_id))
    audit.log(conn, by, "answer", question["case_id"], "done", note, {"question": question_id, "answer": reply})
    case_id = question["case_id"]
    flags = instruction_flags(note)
    if flags:                         # the note is data: only the yes/no/partial reply decides the next step
        audit.log(conn, VOUCH, "suspicious_instruction", question_id, "done",
                  "instruction found in a colleague's note; treated as data and not acted on",
                  {"question": question_id, "case": case_id, "by": by.name, "words": flags})
    conflict = conflicting_answers(conn, case_id, by.name, reply)
    if conflict:
        return _send_conflict_to_controller(conn, case_id, question_id, reply, by, conflict)
    step = question[f"if_{reply}"]
    if step == "release_hold":
        for hold in gates.active_holds(conn):
            if hold["case_id"] == case_id:
                gates.release_hold(conn, by, hold["hold_id"], f"answered {reply}: {note}")
        gates.set_case(conn, by, case_id, "resolved", f"{by.name} answered {reply}: {note}", {"question": question_id})
    elif step == "keep_hold":
        gates.set_case(conn, VOUCH, case_id, "held", f"{by.name} answered {reply}: {note}; the hold stays",
                       {"question": question_id})
    elif step == "draft_correction":
        result = draft(case_id) if draft else None
        gates.set_case(conn, VOUCH, case_id, "held", f"{by.name} answered {reply}; correction drafted and sent for "
                       "approval", {"question": question_id, "draft": result})
    elif step == "resolve_case":
        gates.set_case(conn, VOUCH, case_id, "resolved", f"{by.name} answered {reply}: {note}", {"question": question_id})
    elif step == "ask_follow_up":
        follow = ask(conn, case_id, question["to_role"], question["to_person"],
                     f"Still needed after '{note}': {question['fact_needed']}", question["sources"],
                     question["if_yes"], question["if_no"], "keep_hold", follows=question_id)
        gates.set_case(conn, VOUCH, case_id, "held", f"partial answer; follow-up {follow} asked",
                       {"question": question_id, "follow_up": follow})
    elif step == "escalate_to_controller":
        gates.set_case(conn, VOUCH, case_id, "held", f"{by.name} answered {reply}; sent to the controller",
                       {"question": question_id})
        approvals.request(conn, VOUCH, "escalation", case_id, {"case": case_id, "escalated_from": question_id},
                          case_id=case_id, reason=f"escalated after {by.name} answered {reply}")
    return {"question": question_id, "answer": reply, "next_step": step}


def conflicting_answers(conn, case_id: str, by: str, reply: str) -> list[dict]:
    """Earlier answers on the same case from someone else that say the opposite (yes against no)."""
    if reply not in ("yes", "no"):
        return []
    opposite = "no" if reply == "yes" else "yes"
    return [dict(r) for r in conn.execute(
        "SELECT question_id, answered_by, answer, note FROM questions WHERE case_id = ? AND status = 'answered' "
        "AND answer = ? AND answered_by <> ?", (case_id, opposite, by))]


def _send_conflict_to_controller(conn, case_id: str, question_id: str, reply: str, by: Actor,
                                 conflict: list[dict]) -> dict:
    """Two people disagree: no promised step is taken, anything released is held again, and the
    controller decides."""
    for hold in conn.execute("SELECT * FROM holds WHERE case_id = ? AND released_at IS NOT NULL", (case_id,)).fetchall():
        gates.place_hold(conn, VOUCH, case_id, hold["target_kind"], hold["target_ref"],
                         f"held again: answers disagree ({hold['reason']})", {"conflict": question_id})
    who = "; ".join(f"{c['answered_by']} said {c['answer']} ({c['question_id']})" for c in conflict)
    reason = f"answers disagree: {who}; {by.name} said {reply} ({question_id}); sent to the controller"
    gates.set_case(conn, VOUCH, case_id, "held", reason, {"question": question_id, "conflict": conflict})
    approvals.request(conn, VOUCH, "conflict", case_id,
                      {"case": case_id, "conflict": [c["question_id"] for c in conflict] + [question_id]},
                      case_id=case_id, reason=reason)
    return {"question": question_id, "answer": reply, "next_step": "escalate_to_controller", "conflict": conflict}


def follow_up(conn: sqlite3.Connection) -> list[dict]:
    """Unanswered questions: reminder, then escalation to the controller, on the simulated clock."""
    now, done = clock.now(conn), []
    for question in open_questions(conn):
        waited = clock.business_days_between(question["asked_at"], now)
        if waited >= approvals.ESCALATE_AFTER_BUSINESS_DAYS and not question["escalated_at"]:
            conn.execute("UPDATE questions SET escalated_at = ?, to_role = 'controller' WHERE question_id = ?",
                         (now, question["question_id"]))
            audit.log(conn, VOUCH, "escalate", question["case_id"], "escalated",
                      f"no answer from {question['to_person']} after {waited} business days; asked the controller",
                      {"question": question["question_id"]})
            done.append({"question": question["question_id"], "action": "escalated"})
        elif waited >= approvals.REMIND_AFTER_BUSINESS_DAYS and not question["reminded_at"]:
            conn.execute("UPDATE questions SET reminded_at = ? WHERE question_id = ?", (now, question["question_id"]))
            audit.log(conn, VOUCH, "remind", question["case_id"], "reminded",
                      f"reminder to {question['to_person']}: {question['fact_needed']}", {"question": question["question_id"]})
            done.append({"question": question["question_id"], "action": "reminded"})
    return done
