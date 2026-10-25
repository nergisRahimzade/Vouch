"""Structured requests and attributed answers. Free text never becomes executable arguments."""
import hashlib
import json

from vouch.calc.eventtax import vat_arithmetic
from vouch.controls import audit, clock, gates
from vouch.db.periods import Actor


def request_info(conn, case_id, recipient, about):
    if not recipient:
        raise ValueError("request needs a recorded recipient")
    exists = conn.execute("SELECT id FROM people WHERE id = ? UNION SELECT id FROM vendors WHERE id = ?",
                          (recipient, recipient)).fetchone()
    if not exists:
        raise ValueError("recipient is not a visible person or vendor")
    rid = "INFO-" + hashlib.sha256(json.dumps([case_id, recipient, about]).encode()).hexdigest()[:16]
    cursor = conn.execute("INSERT OR IGNORE INTO info_requests VALUES (?, ?, ?, ?, 'pending', ?, NULL, NULL)",
                           (rid, case_id, recipient, about, clock.now(conn)))
    if cursor.rowcount:
        audit.log(conn, gates.VOUCH, "request_info", case_id, "requested", about,
                  {"request": rid, "to": recipient, "about": about})
    return rid


def answer(conn, request_id, sender, data):
    item = conn.execute("SELECT * FROM info_requests WHERE request_id = ?", (request_id,)).fetchone()
    if not item or item["recipient"] != sender:
        raise PermissionError("only the recorded recipient can answer")
    if item["status"] == "answered":
        return
    if not isinstance(data, dict):
        raise ValueError("answer requires structured facts; prose is untrusted data")
    allowed = {"bewirtung_attendees_occasion": {"participants", "occasion", "host", "place"},
               "gift_recipient_list": {"recipients", "unknown_count"}, "itemized_invoice": {"lines"},
               "attendance": {"employees", "business_partners", "companions"},
               "attendance_list": {"employees", "business_partners", "companions"},
               "nonqualifying_event": {"method", "reason"},
               "handover_draft": {"resolution", "reason"}}
    if item["about"] not in allowed or set(data) - allowed[item["about"]]:
        raise ValueError("answer contains unsupported fields")
    if item["about"] == "gift_recipient_list":
        from decimal import Decimal
        from vouch.world.generator import expand
        recipients = expand(data.get("recipients"))
        unknown = data.get("unknown_count", 0)
        if not isinstance(unknown, int) or isinstance(unknown, bool) or unknown < 0:
            raise ValueError("unknown_count must be an explicitly confirmed non-negative integer")
        lines = conn.execute("SELECT l.qty FROM invoice_lines l JOIN invoices i ON i.id=l.invoice "
                             "WHERE l.category='gift' AND (i.id=? OR i.event=?)", (item["case_id"],item["case_id"])).fetchall()
        if len(lines)!=1 or len(set(recipients))!=len(recipients) or len(recipients)+unknown!=int(Decimal(str(lines[0][0] or 1))):
            raise ValueError("recipient answer must reconcile to the recorded gift quantity")
        known={r[0] for r in conn.execute("SELECT id FROM business_partners")}
        if set(recipients)-known:
            raise ValueError("recipients must identify recorded business-partner people")
    if "lines" in data:
        if not data["lines"] or vat_arithmetic(data["lines"]):
            raise ValueError("itemized invoice arithmetic is invalid")
    if "method" in data:
        _require_tax_authority(conn, sender, data["method"])
    if item["about"] == "handover_draft" and data.get("resolution") not in {"withdraw", "keep"}:
        raise ValueError("a handover answer is resolution 'withdraw' or 'keep'")
    _store(conn, item, sender, data)
    if "method" in data:
        record_ruling(conn, item["case_id"], sender, data["method"], data.get("reason") or "Tax manager ruling",
                      request_id)
    if item["about"] == "handover_draft" and data["resolution"] == "withdraw":
        # The drafter withdraws their own unfinished draft: it is voided on their authority, never deleted.
        from vouch.recon.run import prepass
        role = conn.execute("SELECT role FROM people WHERE id = ?", (sender,)).fetchone()
        for h in prepass(conn, {"type": "process_event", "event": item["case_id"]})["handover"].values():
            if h["drafted_by"] == sender:
                gates.void_draft(conn, Actor(sender, role[0]), h["entry_id"],
                                 data.get("reason") or "Withdrawn by its drafter",
                                 {"request": request_id, "differences": h["differences"]})


PAYROLL_METHODS = {"individual", "flat_40"}


def _require_tax_authority(conn, sender, method):
    """Only the tax manager, or a delegate valid today, decides a payroll method."""
    person = conn.execute("SELECT role FROM people WHERE id = ?", (sender,)).fetchone()
    day = conn.execute("SELECT value FROM scenario WHERE key='as_of'").fetchone()[0]
    delegation = conn.execute("SELECT 1 FROM delegations d JOIN people p ON p.id=d.from_person "
        "WHERE d.to_person=? AND d.role='tax_manager' AND p.role='tax_manager' AND d.valid_from<=? AND d.valid_to>=?",
        (sender, day, day)).fetchone()
    if not person or (person[0] != "tax_manager" and not delegation) or method not in PAYROLL_METHODS:
        raise PermissionError("only the tax manager can decide the payroll method")


def answer_text(conn, request_id, sender, text):
    """A reply in the person's own words. It is kept verbatim and attributed, as untrusted data: it supplies
    no typed facts, and any instruction inside it is flagged, never followed."""
    from vouch.agent.untrusted import instruction_flags
    item = conn.execute("SELECT * FROM info_requests WHERE request_id = ?", (request_id,)).fetchone()
    if not item or item["recipient"] != sender:
        raise PermissionError("only the recorded recipient can answer")
    if item["status"] == "answered":
        return
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a reply needs text")
    _store(conn, item, sender, {"reply": text.strip()}, instructions=instruction_flags(text))


def record_ruling(conn, case_id, sender, method, reason, request_id):
    """A tax-manager decision on the payroll method becomes a learned correction (spec §5.8): followed, never
    re-asked. Immutable once recorded."""
    _require_tax_authority(conn, sender, method)
    cursor = conn.execute("INSERT OR IGNORE INTO learned_corrections VALUES (?, ?, ?, ?, ?, ?, ?)",
                          ("MEM-" + request_id, case_id, sender, method, reason, request_id, clock.now(conn)))
    if cursor.rowcount:
        role = conn.execute("SELECT role FROM people WHERE id = ?", (sender,)).fetchone()
        audit.log(conn, Actor(sender, role[0] if role else "tax_manager"), "ruling", case_id, "done", reason,
                  {"request": request_id, "method": method, "memory": "MEM-" + request_id})


def replies(conn, case_id):
    """Every reply received for a case, in order, with who sent it."""
    rows = conn.execute("SELECT q.request_id, q.recipient, q.about, q.status, f.data, f.received_at FROM info_requests q "
                        "LEFT JOIN received_facts f USING (request_id) WHERE q.case_id = ? ORDER BY q.created_at, q.request_id",
                        (case_id,)).fetchall()
    return [{"request_id": r["request_id"], "to": r["recipient"], "about": r["about"], "status": r["status"],
             "reply": json.loads(r["data"]) if r["data"] else None} for r in rows]


def _store(conn, item, sender, data, instructions=()):
    request_id = item["request_id"]
    encoded = json.dumps(data, sort_keys=True)
    prior = conn.execute("SELECT data FROM received_facts WHERE request_id=?", (request_id,)).fetchone()
    if prior:
        if prior[0] == encoded or conn.execute("SELECT 1 FROM followup_facts WHERE request_id=? AND data=?", (request_id, encoded)).fetchone():
            return
        conn.execute("INSERT INTO followup_facts (request_id,sender,data,received_at) VALUES (?,?,?,?)",
                      (request_id,sender,encoded,clock.now(conn)))
    else:
        conn.execute("INSERT INTO received_facts VALUES (?, ?, ?, ?)",
                      (request_id, sender, encoded, clock.now(conn)))
    prose = set(data) == {"reply"}
    complete = True
    if item["about"] == "bewirtung_attendees_occasion" and not prose:
        # A reply is final only when the recomputed documentation check is complete.
        from vouch.recon.run import prepass
        current = prepass(conn, {"type":"process_event", "event":item["case_id"]})
        complete = not any(f["kind"] == "bewirtung_docs_missing" for f in current["findings"])
    conn.execute("UPDATE info_requests SET status = ? WHERE request_id = ?",
                  ("answered" if complete else "pending", request_id))
    role = conn.execute("SELECT role FROM people WHERE id = ?", (sender,)).fetchone()
    basis = {"request": request_id, "about": item["about"], "fields": sorted(data),
             "complete": complete, "followup": bool(prior)}
    if prose:
        basis.update(reply=data["reply"], untrusted=True, instructions_flagged=list(instructions))
    audit.log(conn, Actor(sender, role[0] if role else "vendor"), "answer", item["case_id"], "done",
              "Reply in the sender's own words recorded" if prose else "Structured response recorded", basis)


REMIND_AFTER_BUSINESS_DAYS, ESCALATE_AFTER_BUSINESS_DAYS = 1, 2     # spec §5.8: 24 business hours, then escalate


def follow_up(conn):
    """Spec §5.8: one reminder after 24 business hours without an answer, then an actual escalation to the tax
    manager (or the valid delegate), who receives an escalation request of their own. Payroll exports are not
    questions (payroll accepts them; the Feb 28 deadline tracks them), and an unanswered escalation to the tax
    manager goes to their delegate rather than back to themselves."""
    from vouch.controls.matrix import delegate
    now, actions = clock.now(conn), []
    tax = conn.execute("SELECT id FROM people WHERE role = 'tax_manager' ORDER BY id").fetchone()
    for row in conn.execute("SELECT * FROM info_requests WHERE status = 'pending' AND about <> 'payroll_export' "
                            "ORDER BY created_at, request_id").fetchall():
        days = clock.business_days_between(row["created_at"], now)
        if days >= ESCALATE_AFTER_BUSINESS_DAYS and row["reminded_at"] and not row["escalated_at"]:
            action = "escalate"
            conn.execute("UPDATE info_requests SET escalated_at = ? WHERE request_id = ?", (now, row["request_id"]))
            to = delegate(conn, tax[0], now[:10]) if tax else None
            if to == row["recipient"]:
                to = conn.execute("SELECT to_person FROM delegations WHERE from_person = ? AND valid_from <= ? "
                                  "AND valid_to >= ? ORDER BY id", (to, now[:10], now[:10])).fetchone()
                to = to[0] if to else None
            escalation = request_info(conn, row["case_id"], to, "overdue_request") if to else None
            basis = {"request": row["request_id"], "waiting_on": row["recipient"], "about": row["about"],
                     "to": to, "escalation_request": escalation, "requested_at": row["created_at"],
                     "reminded_at": row["reminded_at"], "business_days_waited": days}
        elif days >= REMIND_AFTER_BUSINESS_DAYS and not row["reminded_at"]:
            action = "remind"
            conn.execute("UPDATE info_requests SET reminded_at = ? WHERE request_id = ?", (now, row["request_id"]))
            basis = {"request": row["request_id"], "to": row["recipient"], "about": row["about"],
                     "requested_at": row["created_at"], "business_days_waited": days}
        else:
            continue
        audit.log(conn, gates.VOUCH, action, row["case_id"], action + "d", row["about"], basis)
        actions.append({"request_id": row["request_id"], "action": action, "to": basis["to"]})
    return actions
