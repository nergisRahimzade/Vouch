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
               "nonqualifying_event": {"method", "reason"}}
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
        person = conn.execute("SELECT role FROM people WHERE id = ?", (sender,)).fetchone()
        day = conn.execute("SELECT value FROM scenario WHERE key='as_of'").fetchone()[0]
        delegation = conn.execute("SELECT 1 FROM delegations d JOIN people p ON p.id=d.from_person "
            "WHERE d.to_person=? AND d.role='tax_manager' AND p.role='tax_manager' AND d.valid_from<=? AND d.valid_to>=?",
            (sender,day,day)).fetchone()
        if not person or (person[0] != "tax_manager" and not delegation) or data["method"] not in {"individual", "flat_40"}:
            raise PermissionError("only the tax manager can decide the payroll method")
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
    complete = True
    if item["about"] == "bewirtung_attendees_occasion":
        # A reply is final only when the recomputed documentation check is complete.
        from vouch.recon.run import prepass
        current = prepass(conn, {"type":"process_event", "event":item["case_id"]})
        complete = not any(f["kind"] == "bewirtung_docs_missing" for f in current["findings"])
    conn.execute("UPDATE info_requests SET status = ? WHERE request_id = ?",
                  ("answered" if complete else "pending", request_id))
    role = conn.execute("SELECT role FROM people WHERE id = ?", (sender,)).fetchone()
    audit.log(conn, Actor(sender, role[0] if role else "vendor"), "answer", item["case_id"], "done",
              "Structured response recorded", {"request": request_id, "fields": sorted(data)})
    if "method" in data:
        conn.execute("INSERT INTO learned_corrections VALUES (?, ?, ?, ?, ?, ?, ?)",
                      ("MEM-" + request_id, item["case_id"], sender, data["method"], data.get("reason") or "Tax manager ruling",
                       request_id, clock.now(conn)))


def follow_up(conn):
    now, actions = clock.now(conn), []
    for row in conn.execute("SELECT * FROM info_requests WHERE status = 'pending'").fetchall():
        days = clock.business_days_between(row["created_at"], now)
        if days >= 2 and row["reminded_at"] and not row["escalated_at"]:
            action = "escalate"
            conn.execute("UPDATE info_requests SET escalated_at = ? WHERE request_id = ?", (now, row["request_id"]))
        elif days >= 1 and not row["reminded_at"]:
            action = "remind"
            conn.execute("UPDATE info_requests SET reminded_at = ? WHERE request_id = ?", (now, row["request_id"]))
        else:
            continue
        audit.log(conn, gates.VOUCH, action, row["case_id"], action + "d", row["about"],
                  {"request": row["request_id"], "to": "tax_manager" if action == "escalate" else row["recipient"]})
        actions.append({"request_id": row["request_id"], "action": action})
    return actions
