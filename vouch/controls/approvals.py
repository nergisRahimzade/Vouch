"""Approval requests: they wait for the right role in order, and are approved or rejected with
a note. The requester, and whoever keyed the change, can never approve it; an entry at or above
the large-entry limit needs two approvers, one of them the controller."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from decimal import Decimal

from vouch.controls import audit, clock
from vouch.controls.gates import Refused, large_entry_limit, refuse, require
from vouch.db.periods import Actor
from vouch.money import fmt


def fingerprint(kind: str, subject_ref: str, payload: dict) -> str:
    return hashlib.sha256(json.dumps([kind, subject_ref, payload], sort_keys=True, default=str).encode()).hexdigest()[:16]


def needs_for(conn, kind: str, amount: Decimal | None, currency: str | None) -> list[str]:
    if kind == "entry":
        large = amount is not None and currency and amount >= large_entry_limit(conn, currency)
        return ["finance_manager", "controller"] if large else ["finance_manager"]
    return {"payment_release": ["treasury"], "funding": ["treasury"], "bank_change": ["hr"],
            "accrual": ["controller"], "grant": ["stock_admin"], "transfer": ["department_head"],
            "arrears": ["controller"]}[kind]


def request(conn: sqlite3.Connection, actor: Actor, kind: str, subject_ref: str, payload: dict,
            amount: Decimal | None = None, currency: str | None = None, case_id: str | None = None,
            keyed_by: str | None = None, reason: str = "") -> str:
    require(conn, actor, "request_approval", subject_ref)
    print_ = fingerprint(kind, subject_ref, payload)
    rejected = conn.execute("SELECT request_id, note FROM approval_requests WHERE fingerprint = ? AND status = 'rejected'",
                            (print_,)).fetchone()
    if rejected:
        refuse(conn, actor, "request_approval", subject_ref, "PRECEDENT",
               f"the same request was rejected before ({rejected[0]}: {rejected[1]}); change it before asking again",
               {"rejected_request": rejected[0]})
    pending = conn.execute("SELECT request_id FROM approval_requests WHERE fingerprint = ? AND status IN "
                           "('pending', 'escalated', 'approved')", (print_,)).fetchone()
    if pending:
        return pending[0]
    needs = needs_for(conn, kind, amount, currency)
    request_id = f"REQ-{conn.execute('SELECT COUNT(*) FROM approval_requests').fetchone()[0] + 1:04d}"
    now = clock.now(conn)
    conn.execute(
        "INSERT INTO approval_requests (request_id, kind, subject_ref, fingerprint, payload, amount, currency, case_id, "
        "requested_by, keyed_by, needs, status, assigned_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "'pending', ?, ?)",
        (request_id, kind, subject_ref, print_, json.dumps(payload, default=str), fmt(amount) if amount is not None else None,
         currency, case_id, actor.name, keyed_by or actor.name, json.dumps(needs), needs[0], now))
    audit.log(conn, actor, "request_approval", subject_ref, "requested", reason or f"{kind} needs {', '.join(needs)}",
              {"request": request_id, "kind": kind, "needs": needs, "case": case_id,
               "amount": fmt(amount) if amount is not None else None, "currency": currency})
    return request_id


def get(conn, request_id: str) -> dict:
    row = conn.execute("SELECT * FROM approval_requests WHERE request_id = ?", (request_id,)).fetchone()
    if row is None:
        raise KeyError(request_id)
    return {**dict(row), "needs": json.loads(row["needs"]), "approvals": json.loads(row["approvals"])}


def decide(conn: sqlite3.Connection, actor: Actor, request_id: str, decision: str, note: str,
           callback_confirmed: bool = False) -> dict:
    """Approve or reject. Raises Refused if this actor may not decide this request."""
    require(conn, actor, "approve", request_id)
    item = get(conn, request_id)
    if item["status"] not in ("pending", "escalated"):
        refuse(conn, actor, "decide", request_id, "FIN-08", f"the request is already {item['status']}")
    if actor.name == item["requested_by"]:
        refuse(conn, actor, "decide", request_id, "FIN-03", "the requester can never approve their own request")
    if actor.name == item["keyed_by"]:
        refuse(conn, actor, "decide", request_id, "FIN-03", "whoever keyed a change cannot approve it")
    if any(a["actor"] == actor.name for a in item["approvals"]):
        refuse(conn, actor, "decide", request_id, "FIN-04", "two approvals must come from two different people")
    if actor.role != item["assigned_role"]:
        refuse(conn, actor, "decide", request_id, "FIN-04",
               f"this step needs the {item['assigned_role']}, not the {actor.role}")
    if not note.strip():
        refuse(conn, actor, "decide", request_id, "FIN-04", "a decision needs a note")
    now = clock.now(conn)
    if decision == "reject":
        conn.execute("UPDATE approval_requests SET status = 'rejected', closed_at = ?, note = ? WHERE request_id = ?",
                     (now, note, request_id))
        _store_precedent(conn, actor, item, note)
        audit.log(conn, actor, "reject", item["subject_ref"], "done", note, {"request": request_id, "case": item["case_id"]})
        return get(conn, request_id)
    if decision != "approve":
        raise ValueError(f"unknown decision {decision!r}")
    approvals = item["approvals"] + [{"actor": actor.name, "role": actor.role, "at": now, "note": note,
                                      "callback_confirmed": callback_confirmed}]
    done = len(approvals) >= len(item["needs"])
    next_role = item["needs"][len(approvals)] if not done else item["assigned_role"]
    conn.execute("UPDATE approval_requests SET approvals = ?, status = ?, assigned_role = ?, closed_at = ? "
                 "WHERE request_id = ?", (json.dumps(approvals), "approved" if done else "pending", next_role,
                                          now if done else None, request_id))
    audit.log(conn, actor, "approve", item["subject_ref"], "done", note,
              {"request": request_id, "case": item["case_id"], "step": len(approvals), "of": len(item["needs"]),
               "callback_confirmed": callback_confirmed})
    return get(conn, request_id)


def _store_precedent(conn, actor: Actor, item: dict, note: str) -> None:
    """A rejection becomes a ruling every later close must respect."""
    from vouch.recon import rulings
    conn.executescript(rulings.SCHEMA)
    kind = json.loads(item["payload"]).get("case_kind") or item["kind"]
    period = clock.now(conn)[:7]
    conn.execute("INSERT OR IGNORE INTO rulings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (f"RUL-{item['request_id']}", period, clock.now(conn)[:10], kind, None, "rejected", actor.name,
                  json.dumps([]), f"Rejected: {note}", f"approval:{item['request_id']}"))


# Follow-up timing on the simulated clock. Operational defaults chosen for the build (the finance
# policy sets no numbers); awaiting the human's confirmation.
REMIND_AFTER_BUSINESS_DAYS = 2
ESCALATE_AFTER_BUSINESS_DAYS = 4


def follow_up(conn: sqlite3.Connection, actor: Actor) -> list[dict]:
    """Remind, then escalate to the controller, every request left unanswered. Nothing is approved
    by waiting: the request and whatever it holds stay held."""
    now, done = clock.now(conn), []
    for item in [get(conn, r[0]) for r in conn.execute(
            "SELECT request_id FROM approval_requests WHERE status = 'pending' ORDER BY request_id").fetchall()]:
        waited = clock.business_days_between(item["created_at"], now)
        if waited >= ESCALATE_AFTER_BUSINESS_DAYS and not item["escalated_at"]:
            step = len(item["approvals"])
            needs = list(item["needs"])
            original, needs[step] = needs[step], "controller"
            for later in range(step + 1, len(needs)):         # keep two different approvers when one was the controller
                if needs[later] == "controller":
                    needs[later] = original
            conn.execute("UPDATE approval_requests SET status = 'escalated', assigned_role = 'controller', needs = ?, "
                         "escalated_at = ? WHERE request_id = ?", (json.dumps(needs), now, item["request_id"]))
            audit.log(conn, actor, "escalate", item["subject_ref"], "escalated",
                      f"no answer from the {original} after {waited} business days; escalated to the controller",
                      {"request": item["request_id"], "case": item["case_id"], "waited_business_days": waited})
            done.append({"request_id": item["request_id"], "action": "escalated"})
        elif waited >= REMIND_AFTER_BUSINESS_DAYS and not item["reminded_at"]:
            conn.execute("UPDATE approval_requests SET reminded_at = ? WHERE request_id = ?", (now, item["request_id"]))
            audit.log(conn, actor, "remind", item["subject_ref"], "reminded",
                      f"reminder to the {item['assigned_role']}: waiting {waited} business days",
                      {"request": item["request_id"], "case": item["case_id"], "waited_business_days": waited})
            done.append({"request_id": item["request_id"], "action": "reminded"})
    return done


def inbox(conn, role: str) -> list[dict]:
    rows = conn.execute("SELECT request_id FROM approval_requests WHERE assigned_role = ? AND status IN "
                        "('pending', 'escalated') ORDER BY created_at, request_id", (role,))
    return [get(conn, r[0]) for r in rows.fetchall()]


__all__ = ["request", "decide", "get", "inbox", "Refused", "needs_for", "fingerprint"]
