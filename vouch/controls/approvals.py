"""Approval requests: they wait for the right role in order, and are approved or rejected with
a note. The requester, and whoever keyed the change, can never approve it; an entry at or above
the large-entry limit needs two approvers, one of them the controller."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from decimal import Decimal

from vouch.controls import audit, clock
from vouch.controls.gates import Refused, refuse, require
from vouch.db.periods import Actor
from vouch.money import fmt


def fingerprint(kind: str, subject_ref: str, payload: dict) -> str:
    return hashlib.sha256(json.dumps([kind, subject_ref, payload], sort_keys=True, default=str).encode()).hexdigest()[:16]


def needs_for(conn, kind, amount=None, currency=None):
    if kind not in {"entry", "event", "escalation"}:
        raise ValueError("unsupported event-tax approval kind")
    return ["tax_manager"]


def request(conn: sqlite3.Connection, actor: Actor, kind: str, subject_ref: str, payload: dict,
            amount: Decimal | None = None, currency: str | None = None, case_id: str | None = None,
            keyed_by: str | None = None, reason: str = "", required_roles: list | None = None) -> str:
    require(conn, actor, "request_approval", subject_ref)
    if kind == "entry":
        from vouch.controls.gates import stored_fingerprint
        payload = {**payload, "entry_fingerprint": stored_fingerprint(conn, subject_ref)}
    delegated_role = None
    if kind in {"entry", "event", "escalation"}:
        from vouch.controls.matrix import delegate
        tax = conn.execute("SELECT id FROM people WHERE role='tax_manager' ORDER BY id").fetchone()
        if tax:
            deadline = payload.get("deadline") or conn.execute("SELECT value FROM scenario WHERE key='as_of'").fetchone()[0]
            recipient = delegate(conn, tax[0], deadline)
            if recipient != tax[0]:
                role = conn.execute("SELECT role FROM people WHERE id=?", (recipient,)).fetchone()
                if role:
                    delegated_role = role[0]
                    payload = {**payload, "delegation":{"from":tax[0],"to":recipient,"role":"tax_manager","deadline":deadline}}
    print_ = fingerprint(kind, subject_ref, payload)
    rejected = conn.execute("SELECT request_id, note FROM approval_requests WHERE fingerprint = ? AND status = 'rejected'",
                            (print_,)).fetchone()
    if rejected is None and payload.get("entry_fingerprint"):
        # The same booking re-drafted under a new id is still the booking the reviewer said no to.
        rejected = next(((r["request_id"], r["note"]) for r in conn.execute(
            "SELECT request_id, note, payload FROM approval_requests WHERE kind = 'entry' AND status = 'rejected'")
            if json.loads(r["payload"]).get("entry_fingerprint") == payload["entry_fingerprint"]), None)
    if rejected:
        refuse(conn, actor, "request_approval", subject_ref, "PRECEDENT",
               f"the same request was rejected before ({rejected[0]}: {rejected[1]}); change it before asking again",
               {"rejected_request": rejected[0]})
    pending = conn.execute("SELECT request_id FROM approval_requests WHERE (fingerprint = ? OR (kind = ? AND "
                           "subject_ref = ?)) AND status IN ('pending', 'escalated', 'approved')",
                           (print_, kind, subject_ref)).fetchone()     # one live request per subject: no duplicates
    if pending:
        return pending[0]
    needs = needs_for(conn, kind, amount, currency)
    if required_roles is not None:
        if not required_roles or set(required_roles) - {"tax_manager", "controller"} or "tax_manager" not in required_roles:
            raise ValueError("approval roles must include tax_manager and optionally controller")
        needs = ["tax_manager"] + (["controller"] if "controller" in required_roles else [])
    if kind == "entry":
        entry = conn.execute("SELECT type FROM journal_entries WHERE entry_id = ? AND status = 'draft'",
                             (subject_ref,)).fetchone()
        if not entry:
            raise ValueError("entry approval requires an existing draft")
        # POL-MAT measures correction entries, not all invoice values.
        if entry[0] in {"reclass", "vat_reversal"} and amount is not None and amount >= Decimal("5000"):
            if "controller" not in needs:
                needs.append("controller")
    assigned_role = delegated_role or needs[0]
    request_id = f"REQ-{conn.execute('SELECT COUNT(*) FROM approval_requests').fetchone()[0] + 1:04d}"
    now = clock.now(conn)
    conn.execute(
        "INSERT INTO approval_requests (request_id, kind, subject_ref, fingerprint, payload, amount, currency, case_id, "
        "requested_by, keyed_by, needs, status, assigned_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "'pending', ?, ?)",
        (request_id, kind, subject_ref, print_, json.dumps(payload, default=str), fmt(amount) if amount is not None else None,
         currency, case_id, actor.name, keyed_by or actor.name, json.dumps(needs), assigned_role, now))
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
    if done and item.get("case_id"):
        from vouch.controls.gates import active_holds, release_hold
        for hold in active_holds(conn):
            if hold["case_id"] == item["case_id"]:
                release_hold(conn, actor, hold["hold_id"], "Evidence reviewed and final approval recorded: " + note)
    return get(conn, request_id)


def _store_precedent(conn, actor, item, note):
    conn.execute("CREATE TABLE IF NOT EXISTS approval_rejections "
                 "(request_id TEXT PRIMARY KEY, actor TEXT, note TEXT, at TEXT)")
    conn.execute("INSERT INTO approval_rejections VALUES (?, ?, ?, ?)",
                 (item["request_id"], actor.name, note, clock.now(conn)))


REMIND_AFTER_BUSINESS_DAYS = 1
ESCALATE_AFTER_BUSINESS_DAYS = 2


def follow_up(conn, actor):
    require(conn, actor, "request_approval", "follow_up")
    now, done = clock.now(conn), []
    for item in [get(conn, r[0]) for r in conn.execute(
            "SELECT request_id FROM approval_requests WHERE status = 'pending'").fetchall()]:
        waited = clock.business_days_between(item["created_at"], now)
        waiting_on = conn.execute("SELECT id FROM people WHERE role = ? ORDER BY id", (item["assigned_role"],)).fetchone()
        if waited >= ESCALATE_AFTER_BUSINESS_DAYS and item["reminded_at"] and not item["escalated_at"]:
            # An escalation never counts as an approval and never replaces the controller's step. A tax
            # manager out of office past the deadline hands the step to the recorded delegate.
            from vouch.controls.matrix import delegate
            reassigned = None
            if item["assigned_role"] == "tax_manager" and waiting_on:
                stand_in = delegate(conn, waiting_on[0], now[:10])
                if stand_in != waiting_on[0]:
                    reassigned = conn.execute("SELECT role FROM people WHERE id = ?", (stand_in,)).fetchone()[0]
            escalate_to = "cfo" if item["assigned_role"] == "tax_manager" else "tax_manager"
            conn.execute("UPDATE approval_requests SET status = 'escalated', escalated_at = ?, assigned_role = ? "
                          "WHERE request_id = ?", (now, reassigned or item["assigned_role"], item["request_id"]))
            audit.log(conn, actor, "escalate", item["subject_ref"], "escalated",
                      f"no reply from the {item['assigned_role']} {waited} business days after the request and a reminder",
                      {"request": item["request_id"], "waiting_on": item["assigned_role"],
                       "waiting_on_person": waiting_on[0] if waiting_on else None, "to": escalate_to,
                       "reassigned_to_role": reassigned, "requested_at": item["created_at"],
                       "reminded_at": item["reminded_at"], "business_days_waited": waited})
            done.append({"request_id": item["request_id"], "action": "escalated"})
        elif waited >= REMIND_AFTER_BUSINESS_DAYS and not item["reminded_at"]:
            conn.execute("UPDATE approval_requests SET reminded_at = ? WHERE request_id = ?",
                          (now, item["request_id"]))
            audit.log(conn, actor, "remind", item["subject_ref"], "reminded", "waiting for an answer",
                      {"request": item["request_id"], "to": item["assigned_role"],
                       "to_person": waiting_on[0] if waiting_on else None, "requested_at": item["created_at"],
                       "business_days_waited": waited})
            done.append({"request_id": item["request_id"], "action": "reminded"})
    return done


def inbox(conn, role: str) -> list[dict]:
    rows = conn.execute("SELECT request_id FROM approval_requests WHERE assigned_role = ? AND status IN "
                        "('pending', 'escalated') ORDER BY created_at, request_id", (role,))
    return [get(conn, r[0]) for r in rows.fetchall()]


__all__ = ["request", "decide", "get", "inbox", "Refused", "needs_for", "fingerprint"]


def standing_policy_request(conn, entry_id, computed, case_id, keyed_by=None):
    """Apply P-TAX's standing POL-MAT approval only to a verified clean computed proposal."""
    from vouch.controls.matrix import route
    from vouch.controls.gates import VOUCH, stored_fingerprint
    actual = route(flags=computed["flags"], wage_taxable=computed["wage_taxable"],
                   nondeductible=computed["nondeductible"], flat_tax_37b=computed["flat_tax_37b"],
                   corrections=computed["corrections"], blocked=computed["decision"] != "classify_and_book")
    if actual["approvers"] != ["policy"]:
        raise ValueError("proposal does not satisfy standing POL-MAT approval")
    entry = conn.execute("SELECT status FROM journal_entries WHERE entry_id=?",(entry_id,)).fetchone()
    if not entry or entry[0] != "draft":
        raise ValueError("standing policy applies to an existing draft")
    existing=conn.execute("SELECT request_id FROM approval_requests WHERE subject_ref=? AND kind='entry'",(entry_id,)).fetchone()
    if existing:
        return existing[0]
    payload={"entry":entry_id,"entry_fingerprint":stored_fingerprint(conn,entry_id),"policy":"POL-MAT"}
    rid=f"REQ-{conn.execute('SELECT COUNT(*) FROM approval_requests').fetchone()[0]+1:04d}"
    owner=conn.execute("SELECT owner FROM policies WHERE id='POL-MAT'").fetchone()[0]
    now=clock.now(conn)
    approvals_=[{"actor":owner,"role":"policy","at":now,"note":"Standing POL-MAT approval"}]
    conn.execute("INSERT INTO approval_requests (request_id,kind,subject_ref,fingerprint,payload,case_id,requested_by,keyed_by,"
                 "needs,approvals,status,assigned_role,created_at,closed_at,note) VALUES (?, 'entry', ?, ?, ?, ?, 'Vouch',?,"
                 "?,?,'approved','policy',?,?,?)",(rid,entry_id,fingerprint("entry",entry_id,payload),json.dumps(payload),case_id,
                 keyed_by or "Vouch",json.dumps(["policy"]),json.dumps(approvals_),now,now,"Standing POL-MAT approval"))
    audit.log(conn,VOUCH,"apply_standing_policy",entry_id,"done","Clean proposal covered by P-TAX standing policy",
              {"request":rid,"policy":"POL-MAT","owner":owner,"entry_fingerprint":payload["entry_fingerprint"]})
    return rid
