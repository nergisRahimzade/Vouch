"""Gates in code. Every write checks the actor's role and the control rules before it happens;
a refusal is written to the audit trail and raised. Vouch gets a junior accountant's access:
it reads everything and drafts, but people approve, post, reopen and release money."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from vouch.controls import audit, clock
from vouch.db import periods
from vouch.db.ledger import Entry, post_entry
from vouch.db.periods import Actor
from vouch.money import ZERO, fmt

VOUCH = Actor("Vouch", "vouch")

PERMISSIONS = {
    "vouch": {"read", "draft_entry", "open_case", "place_hold", "ask_question", "request_approval",
              "request_funding", "resolve_case"},
    "controller": {"read", "approve", "post_entry", "reopen_period", "lock_period", "sign_off", "release_hold",
                   "answer", "request_approval"},
    "finance_manager": {"read", "approve", "answer", "request_approval"},
    "hr": {"read", "approve", "release_hold", "answer"},
    "treasury": {"read", "approve", "release_payment", "release_hold", "answer"},
    "stock_admin": {"read", "approve", "release_hold", "answer"},
    "department_head": {"read", "approve", "answer", "request_approval"},
}


class Refused(PermissionError):
    def __init__(self, rule: str, reason: str):
        super().__init__(f"refused ({rule}): {reason}")
        self.rule, self.reason = rule, reason


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    clock.now(conn)


def refuse(conn, actor: Actor, action: str, target: str, rule: str, reason: str, basis: dict | None = None):
    audit.log(conn, actor, action, target, "refused", reason, {"rule": rule, **(basis or {})})
    conn.commit()
    raise Refused(rule, reason)


def require(conn, actor: Actor, action: str, target: str) -> None:
    if action not in PERMISSIONS.get(actor.role, set()):
        refuse(conn, actor, action, target, "ROLE", f"{actor.name} ({actor.role}) is not allowed to {action}")


def _rule_text(conn, rule_id: str) -> str:
    for (content,) in conn.execute("SELECT content FROM documents WHERE kind IN ('pay_policy', 'finance_policy')"):
        for rule in json.loads(content)["rules"]:
            if rule["rule_id"] == rule_id:
                return rule
    raise KeyError(rule_id)


def large_entry_limit(conn, currency: str) -> Decimal:
    return Decimal(_rule_text(conn, "FIN-04")["limit"][currency])


# -- cases and holds ------------------------------------------------------------
def open_case(conn, actor: Actor, case_id: str, close_period: str, kind: str, summary: str) -> None:
    require(conn, actor, "open_case", case_id)
    now = clock.now(conn)
    cursor = conn.execute("INSERT OR IGNORE INTO cases VALUES (?, ?, ?, 'open', ?, ?, ?, '')",
                          (case_id, close_period, kind, summary, now, now))
    if cursor.rowcount:
        audit.log(conn, actor, "open_case", case_id, "done", summary, {"finding": case_id})


def set_case(conn, actor: Actor, case_id: str, status: str, note: str, basis: dict | None = None) -> None:
    conn.execute("UPDATE cases SET status = ?, note = ?, updated_at = ? WHERE case_id = ?",
                 (status, note, clock.now(conn), case_id))
    audit.log(conn, actor, f"case_{status}", case_id, "done", note, basis)


def active_holds(conn, target_kind: str | None = None, target_ref: str | None = None) -> list[dict]:
    query = "SELECT * FROM holds WHERE released_at IS NULL"
    params = []
    if target_kind:
        query += " AND target_kind = ?"
        params.append(target_kind)
    if target_ref:
        query += " AND target_ref = ?"
        params.append(target_ref)
    return [dict(r) for r in conn.execute(query, params)]


def place_hold(conn, actor: Actor, case_id: str, target_kind: str, target_ref: str, reason: str,
               basis: dict | None = None) -> int:
    require(conn, actor, "place_hold", target_ref)
    existing = [h for h in active_holds(conn, target_kind, target_ref) if h["case_id"] == case_id]
    if existing:
        return existing[0]["hold_id"]
    cursor = conn.execute("INSERT INTO holds (case_id, target_kind, target_ref, reason, placed_by, placed_at) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (case_id, target_kind, target_ref, reason, actor.name,
                                                         clock.now(conn)))
    audit.log(conn, actor, "place_hold", f"{target_kind}:{target_ref}", "done", reason, {"case": case_id, **(basis or {})})
    return cursor.lastrowid


def release_hold(conn, actor: Actor, hold_id: int, note: str) -> None:
    require(conn, actor, "release_hold", f"hold:{hold_id}")
    hold = conn.execute("SELECT * FROM holds WHERE hold_id = ?", (hold_id,)).fetchone()
    if hold["placed_by"] == actor.name:
        refuse(conn, actor, "release_hold", f"hold:{hold_id}", "FIN-03", "whoever placed a hold cannot release it")
    conn.execute("UPDATE holds SET released_by = ?, released_at = ?, release_note = ? WHERE hold_id = ?",
                 (actor.name, clock.now(conn), note, hold_id))
    audit.log(conn, actor, "release_hold", f"{hold['target_kind']}:{hold['target_ref']}", "done", note,
              {"hold": hold_id, "case": hold["case_id"]})


# -- entries --------------------------------------------------------------------
def entry_fingerprint(entry: Entry) -> str:
    lines = sorted((l.account, l.department or "", fmt(l.debit), fmt(l.credit)) for l in entry.lines)
    return hashlib.sha256(json.dumps([entry.entity, entry.period, lines]).encode()).hexdigest()[:16]


def _existing_fingerprints(conn, entity: str, period: str) -> dict[str, str]:
    out = {}
    entries = conn.execute("SELECT entry_id FROM journal_entries WHERE entity = ? AND period = ?", (entity, period))
    for (entry_id,) in entries.fetchall():
        lines = conn.execute("SELECT account, department, debit, credit FROM journal_lines WHERE entry_id = ?",
                             (entry_id,)).fetchall()
        key = sorted((r[0], r[1] or "", r[2], r[3]) for r in lines)
        out[hashlib.sha256(json.dumps([entity, period, key]).encode()).hexdigest()[:16]] = entry_id
    return out


def _unapproved_grants(conn, entry: Entry) -> list[str]:
    approved = {json.loads(d)["grant_id"] for (d,) in conn.execute(
        "SELECT r.data FROM source_rows r JOIN documents d USING (doc_id) WHERE d.kind = 'board_approvals'")}
    bad = []
    for line in entry.lines:
        if line.account != "6300":
            continue
        for source in line.sources:
            if source.row_no is None:
                continue
            row = conn.execute("SELECT d.kind, r.data FROM source_rows r JOIN documents d USING (doc_id) "
                               "WHERE r.doc_id = ? AND r.row_no = ?", (source.doc_id, source.row_no)).fetchone()
            if row and row[0] == "equity_report":
                grant = json.loads(row[1])["grant_id"]
                if grant not in approved:
                    bad.append(grant)
    return bad


def draft_entry(conn, actor: Actor, entry: Entry, case_id: str | None = None) -> str:
    """Write a draft. Drafts never post themselves; posting needs an approved request and the controller."""
    require(conn, actor, "draft_entry", entry.entry_id)
    basis = {"case": case_id, "entity": entry.entity, "period": entry.period}
    if entry.status != "draft":
        refuse(conn, actor, "draft_entry", entry.entry_id, "ROLE", "Vouch can only draft; posting is the controller's")
    if periods.status(conn, entry.period) == "locked":
        refuse(conn, actor, "draft_entry", entry.entry_id, "FIN-05",
               f"{entry.period} is locked; book the correction in the open month", basis)
    unapproved = _unapproved_grants(conn, entry)
    if unapproved:
        refuse(conn, actor, "draft_entry", entry.entry_id, "FIN-07",
               f"no expense for a grant without board approval: {', '.join(sorted(set(unapproved)))}", basis)
    duplicate = _existing_fingerprints(conn, entry.entity, entry.period).get(entry_fingerprint(entry))
    if duplicate:
        refuse(conn, actor, "draft_entry", entry.entry_id, "FIN-08", f"repeats entry {duplicate} line for line", basis)
    post_entry(conn, entry)
    total = sum((l.debit for l in entry.lines), ZERO)
    audit.log(conn, actor, "draft_entry", entry.entry_id, "done", entry.reason,
              {**basis, "debits": fmt(total), "lines": [(l.account, l.department, fmt(l.debit), fmt(l.credit))
                                                         for l in entry.lines]})
    return entry.entry_id


def post_approved_entry(conn, actor: Actor, entry_id: str) -> None:
    require(conn, actor, "post_entry", entry_id)
    request = conn.execute("SELECT * FROM approval_requests WHERE kind = 'entry' AND subject_ref = ? "
                           "ORDER BY created_at DESC LIMIT 1", (entry_id,)).fetchone()
    if request is None or request["status"] != "approved":
        refuse(conn, actor, "post_entry", entry_id, "FIN-04", "the entry has no approved request")
    period = conn.execute("SELECT period FROM journal_entries WHERE entry_id = ?", (entry_id,)).fetchone()[0]
    if periods.status(conn, period) == "locked":
        refuse(conn, actor, "post_entry", entry_id, "FIN-05", f"{period} is locked")
    conn.execute("UPDATE journal_entries SET status = 'posted' WHERE entry_id = ?", (entry_id,))
    audit.log(conn, actor, "post_entry", entry_id, "done", "posted after approval", {"request": request["request_id"]})


def reopen_period(conn, actor: Actor, period: str, reason: str) -> None:
    require(conn, actor, "reopen_period", period)
    periods.reopen_period(conn, period, actor, reason)
    audit.log(conn, actor, "reopen_period", period, "done", reason, {"rule": "FIN-05"})


# -- money ----------------------------------------------------------------------
def release_payment(conn, actor: Actor, payment_id: str, note: str = "") -> None:
    require(conn, actor, "release_payment", payment_id)
    holds = active_holds(conn, "payment", payment_id)
    if holds:
        refuse(conn, actor, "release_payment", payment_id, "PAY-06" if "bank" in holds[0]["reason"].lower() else "FIN-06",
               f"payment is held: {holds[0]['reason']}", {"hold": holds[0]["hold_id"]})
    if conn.execute("SELECT 1 FROM payment_releases WHERE payment_id = ?", (payment_id,)).fetchone():
        refuse(conn, actor, "release_payment", payment_id, "FIN-08", "this payment was already released")
    conn.execute("INSERT INTO payment_releases VALUES (?, ?, ?, ?)", (payment_id, actor.name, clock.now(conn), note))
    audit.log(conn, actor, "release_payment", payment_id, "done", note or "released")


def apply_bank_change(conn, actor: Actor, request_id: str) -> None:
    """A bank detail change takes effect only after HR approved it and confirmed a call-back."""
    require(conn, actor, "approve", request_id)
    request = conn.execute("SELECT * FROM approval_requests WHERE request_id = ?", (request_id,)).fetchone()
    approvals = json.loads(request["approvals"])
    by_hr = [a for a in approvals if a["role"] == "hr" and a.get("callback_confirmed")]
    if request["kind"] != "bank_change" or request["status"] != "approved" or not by_hr:
        refuse(conn, actor, "apply_bank_change", request_id, "PAY-06",
               "a bank detail change needs HR's approval and a call-back to the number on file")
    audit.log(conn, actor, "apply_bank_change", request["subject_ref"], "done", "HR approved after a call-back",
              {"request": request_id, "confirmed_by": by_hr[0]["actor"]})
