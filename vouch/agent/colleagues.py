"""Simulated colleagues for the demo: approvers who answer Vouch's questions and decide its
requests, and an auditor who asks Vouch to defend its calls. A Claude model can play them, with
a scripted fallback. They act only through the Layer 5 functions, as the people they play, so
every control and audit rule applies to them exactly as to real people."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from vouch.agent import questions
from vouch.agent.defend import why
from vouch.agent.policy import PolicyFailed
from vouch.agent.tools import Toolbox
from vouch.controls import approvals, audit, gates
from vouch.controls.gates import Refused
from vouch.db.periods import directory_actor


@dataclass
class Reply:
    answer: str                 # yes | no | partial (questions) or approve | reject | wait (requests)
    note: str
    callback_confirmed: bool = False


class ScriptedColleagues:
    """Fixed answers by kind of case and role. The fallback, and the fast-test mode."""
    mode = "scripted"

    ANSWERS = {
        ("bank_details_differ_from_hr", "hr"):
            Reply("no", "Called the number on file: she never asked for a change and the email is not hers."),
        ("bank_account_belonged_to_someone_else", "hr"):
            Reply("no", "Call-back made: the account is not the new hire's; new details requested."),
        ("paid_not_in_hr", "hr"): Reply("no", "There is no such employee; do not pay."),
        ("paid_after_exit", "hr"): Reply("yes", "The exit date in HR is correct."),
        ("entity_differs_from_hr", "department_head"):
            Reply("partial", "The move is real, but the effective date is not agreed yet."),
        ("grant_without_board_approval", "stock_admin"):
            Reply("no", "The board has not approved it; it goes to the next meeting."),
        ("forfeiture_not_recorded", "stock_admin"): Reply("yes", "Forfeiture now recorded on the platform."),
        ("cash_booked_but_not_in_bank", "treasury"):
            Reply("no", "The bank never paid it; the booking came from the payment file."),
        ("duplicate_payment", "treasury"): Reply("yes", "The duplicate was cancelled before release."),
    }

    def answer_question(self, question: dict, case: dict | None) -> Reply:
        kind = case["kind"] if case else ""
        return self.ANSWERS.get((kind, question["to_role"]), Reply("partial", "I need more detail before I can answer."))

    def decide_request(self, request: dict, case: dict | None) -> Reply:
        role, kind = request["assigned_role"], request["kind"]
        if kind == "entry":
            return Reply("approve", "Checked against the register and the sources." if role == "finance_manager"
                         else "Reviewed as second approver.")
        if kind == "funding":
            return Reply("approve", "Re-sending only the unpaid lines.")
        if kind == "arrears":
            return Reply("approve", "Pay the arrears in the open month.")
        if kind == "accrual" and case and case["kind"] == "approval_breaks_policy":
            return Reply("reject", "Self-approved accrual; resubmit with a different requester and the controller.")
        return Reply("wait", "Not yet.")

    def audit_question(self, case: dict) -> str:
        return f"Why did you handle the {case['kind'].replace('_', ' ')} case this way, and what did you rely on?"


ANSWER_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string", "enum": ["yes", "no", "partial"]}, "note": {"type": "string"},
    "callback_confirmed": {"type": "boolean"}}, "required": ["answer", "note", "callback_confirmed"],
    "additionalProperties": False}
DECISION_SCHEMA = {"type": "object", "properties": {
    "decision": {"type": "string", "enum": ["approve", "reject", "wait"]}, "note": {"type": "string"}},
    "required": ["decision", "note"], "additionalProperties": False}

PERSONA = """You play {name}, the {title} at Northlight Labs, in a demo of an accounting agent. You know only the case
facts shown. Answer as a careful colleague in that role would: confirm only what the facts support; if a call-back
or approval cannot be confirmed from the facts, say no or partial. Keep the note to one or two sentences."""


class ModelColleagues:
    """A Claude model plays each colleague; any failure falls back to the scripted answer (logged)."""
    mode = "model"

    def __init__(self, policy, conn: sqlite3.Connection):
        self.policy, self.conn, self.fallback = policy, conn, ScriptedColleagues()
        self.people = {p["role"] + (":" + p.get("department", "") if p["role"] == "department_head" else ""): p
                       for p in json.loads(conn.execute("SELECT content FROM documents WHERE kind = 'directory'")
                                           .fetchone()[0])["people"]}

    def _person(self, role: str, case: dict | None) -> dict:
        if role == "department_head":
            return self.people.get(f"department_head:{(case or {}).get('subject', {}).get('department', '')}") or \
                next(p for k, p in self.people.items() if k.startswith("department_head"))
        return self.people[role]

    def _ask(self, person: dict, prompt: str, schema: dict) -> dict:
        response = self.policy._create(system=PERSONA.format(name=person["name"], title=person["title"]),
                                       messages=[{"role": "user", "content": prompt}],
                                       output_format={"type": "json_schema", "schema": schema})
        return json.loads(next(b.text for b in response.content if b.type == "text"))

    def _facts(self, case: dict | None) -> str:
        if not case:
            return "(no case facts)"
        return json.dumps({k: case.get(k) for k in ("kind", "summary", "evidence", "trusted_source", "amount",
                                                     "currency", "rules")}, default=str)

    def answer_question(self, question: dict, case: dict | None) -> Reply:
        try:
            data = self._ask(self._person(question["to_role"], case),
                             f"Vouch asks you: {question['fact_needed']}\nSources it says disagree: "
                             f"{question['sources']}\nCase facts: {self._facts(case)}", ANSWER_SCHEMA)
            return Reply(data["answer"], data["note"], data["callback_confirmed"])
        except (PolicyFailed, ValueError, StopIteration, KeyError) as error:
            audit.log(self.conn, gates.VOUCH, "colleague_fallback", question["case_id"], "done",
                      f"model colleague unavailable ({error}); scripted answer used")
            return self.fallback.answer_question(question, case)

    def decide_request(self, request: dict, case: dict | None) -> Reply:
        try:
            data = self._ask(self._person(request["assigned_role"], case),
                             f"Approval request {request['request_id']} ({request['kind']}) for {request['subject_ref']}"
                             f", amount {request['amount']} {request['currency']}, requested by "
                             f"{request['requested_by']}.\nCase facts: {self._facts(case)}\nApprove, reject or wait?",
                             DECISION_SCHEMA)
            return Reply(data["decision"], data["note"])
        except (PolicyFailed, ValueError, StopIteration, KeyError) as error:
            audit.log(self.conn, gates.VOUCH, "colleague_fallback", request["request_id"], "done",
                      f"model colleague unavailable ({error}); scripted decision used")
            return self.fallback.decide_request(request, case)

    def audit_question(self, case: dict) -> str:
        try:
            data = self._ask({"name": "the external auditor", "title": "auditor"},
                             f"Write one probing question asking Vouch to justify this case.\nCase facts: "
                             f"{self._facts(case)}", {"type": "object", "properties": {"question": {"type": "string"}},
                                                     "required": ["question"], "additionalProperties": False})
            return data["question"]
        except (PolicyFailed, ValueError, StopIteration, KeyError):
            return self.fallback.audit_question(case)


def respond(conn: sqlite3.Connection, colleagues) -> dict:
    """One round: colleagues answer open questions and decide waiting requests; the controller
    posts entries whose approvals are complete; treasury releases payments that are not held."""
    toolbox = Toolbox(conn)
    cases = toolbox.cases
    answered, decided, posted, released, refused = [], [], [], [], []

    def draft(case_id):
        entry = toolbox.draft_for(case_id)
        toolbox.call("request_approval", {"case_id": case_id, "kind": "entry", "subject_ref": entry,
                                          "note": "drafted after the answer"})
        return entry

    for question in questions.open_questions(conn):
        reply = colleagues.answer_question(question, cases.get(question["case_id"]))
        person = directory_actor(conn, question["to_role"]) if question["to_role"] != "department_head" else \
            gates.Actor(question["to_person"], "department_head")
        result = questions.answer(conn, question["question_id"], person, reply.answer, reply.note, draft=draft)
        answered.append(result)
    for _ in range(3):                                  # entries may need two approvals in turn
        progress = False
        for request in [approvals.get(conn, r[0]) for r in conn.execute(
                "SELECT request_id FROM approval_requests WHERE status IN ('pending', 'escalated')").fetchall()]:
            reply = colleagues.decide_request(request, cases.get(request["case_id"] or ""))
            if reply.answer == "wait":
                continue
            role = request["assigned_role"]
            person = directory_actor(conn, role) if role != "department_head" else \
                gates.Actor(colleagues_department_head(conn, cases.get(request["case_id"] or "")), role)
            try:
                approvals.decide(conn, person, request["request_id"], reply.answer, reply.note, reply.callback_confirmed)
                decided.append({"request": request["request_id"], "decision": reply.answer, "by": person.name})
                progress = True
            except Refused as refusal:
                refused.append({"request": request["request_id"], "rule": refusal.rule, "reason": refusal.reason})
        if not progress:
            break
    controller = directory_actor(conn, "controller")
    for (entry_id,) in conn.execute("SELECT subject_ref FROM approval_requests WHERE kind = 'entry' AND status = "
                                    "'approved'").fetchall():
        if conn.execute("SELECT status FROM journal_entries WHERE entry_id = ?", (entry_id,)).fetchone()[0] == "draft":
            gates.post_approved_entry(conn, controller, entry_id)
            posted.append(entry_id)
    treasury = directory_actor(conn, "treasury")
    month = toolbox.src.open_month
    for payment in toolbox.src.rows("payment_file", month):
        if payment["status"] != "pending_release" or conn.execute(
                "SELECT 1 FROM payment_releases WHERE payment_id = ?", (payment["payment_id"],)).fetchone():
            continue
        try:
            gates.release_payment(conn, treasury, payment["payment_id"], "released in the payment run")
            released.append(payment["payment_id"])
        except Refused as refusal:
            refused.append({"payment": payment["payment_id"], "rule": refusal.rule, "reason": refusal.reason})
    conn.commit()
    return {"mode": colleagues.mode, "answered": answered, "decided": decided, "posted": posted,
            "released": len(released), "refused": refused}


def colleagues_department_head(conn, case: dict | None) -> str:
    people = json.loads(conn.execute("SELECT content FROM documents WHERE kind = 'directory'").fetchone()[0])["people"]
    department = (case or {}).get("subject", {}).get("department")
    heads = [p for p in people if p["role"] == "department_head"]
    return next((p["name"] for p in heads if p.get("department") == department), heads[0]["name"])


def auditor_review(conn: sqlite3.Connection, colleagues, policy, limit: int = 3) -> list[dict]:
    """The auditor asks Vouch to defend a few cases; Vouch answers from the audit trail only."""
    toolbox = Toolbox(conn)
    out = []
    for case in list(toolbox.cases.values())[:limit]:
        question = colleagues.audit_question(case)
        out.append({"case_id": case["finding_id"], "question": question,
                    **why(conn, case["finding_id"], question, policy)})
    return out
