"""Role gates for event-tax drafts, posting and append-only decisions."""
import hashlib
import json
from pathlib import Path

from vouch.controls import audit, clock
from vouch.db import periods
from vouch.db.ledger import post_entry
from vouch.db.periods import Actor
from vouch.money import fmt

VOUCH = Actor("Vouch", "vouch")
PERMISSIONS = {
    "vouch": {"read", "draft_entry", "open_case", "place_hold", "ask_question", "request_approval", "resolve_case",
              "void_draft"},
    "tax_manager": {"read", "approve", "answer", "request_approval", "release_hold", "void_draft"},
    "controller": {"read", "approve", "answer", "post_entry", "lock_period", "request_approval", "release_hold", "void_draft"},
    "cfo": {"read", "approve", "answer", "request_approval", "release_hold", "void_draft"},
    "accountant": {"read", "answer", "void_draft"},      # a colleague who drafts and may hand work over half done
    "payroll": {"read", "answer"}, "organizer": {"read", "answer"}, "vendor": {"read", "answer"}}


class Refused(PermissionError):
    def __init__(self, rule, reason):
        super().__init__(f"refused ({rule}): {reason}")
        self.rule, self.reason = rule, reason


def ensure(conn):
    conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    if "recorded_at" not in {r[1] for r in conn.execute("PRAGMA table_info(audit_log)")}:
        conn.execute("ALTER TABLE audit_log ADD COLUMN recorded_at TEXT")    # databases made before the column
    conn.executescript((Path(__file__).parents[1] / "db" / "append_only.sql").read_text(encoding="utf-8"))
    clock.now(conn)


def refuse(conn, actor, action, target, rule, reason, basis=None):
    audit.log(conn, actor, action, target, "refused", reason, {"rule": rule, **(basis or {})})
    conn.commit()
    raise Refused(rule, reason)


def require(conn, actor, action, target):
    if action not in PERMISSIONS.get(actor.role, set()):
        refuse(conn, actor, action, target, "ROLE", f"{actor.role} may not {action}")
    if actor.role != "vouch":
        person = conn.execute("SELECT 1 FROM people WHERE (id = ? OR name = ?) AND role = ?",
                               (actor.name, actor.name, actor.role)).fetchone()
        if not person:
            refuse(conn, actor, action, target, "IDENTITY", "actor is not a recorded person in that role")


def open_case(conn, actor, case_id, close_period, kind, summary, finding=None):
    require(conn, actor, "open_case", case_id)
    now = clock.now(conn)
    cursor = conn.execute("INSERT OR IGNORE INTO cases VALUES (?, ?, ?, 'open', ?, ?, ?, '')",
                           (case_id, close_period, kind, summary, now, now))
    if cursor.rowcount:
        audit.log(conn, actor, "open_case", case_id, "done", summary, {"finding": finding or {}})


def set_case(conn, actor, case_id, status, note, basis=None):
    require(conn, actor, "resolve_case", case_id)
    if status not in {"open", "held", "waiting", "resolved"}:
        raise ValueError("unknown case status")
    conn.execute("UPDATE cases SET status = ?, note = ?, updated_at = ? WHERE case_id = ?",
                  (status, note, clock.now(conn), case_id))
    audit.log(conn, actor, f"case_{status}", case_id, "done", note, basis)


def active_holds(conn, target_kind=None, target_ref=None):
    return [dict(r) for r in conn.execute("SELECT * FROM holds WHERE released_at IS NULL")
            if (target_kind is None or r["target_kind"] == target_kind)
            and (target_ref is None or r["target_ref"] == target_ref)]


def place_hold(conn, actor, case_id, target_kind, target_ref, reason, basis=None):
    require(conn, actor, "place_hold", target_ref)
    existing = [h for h in active_holds(conn, target_kind, target_ref) if h["case_id"] == case_id]
    if existing:
        return existing[0]["hold_id"]
    cursor = conn.execute("INSERT INTO holds (case_id, target_kind, target_ref, reason, placed_by, placed_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (case_id, target_kind, target_ref, reason, actor.name, clock.now(conn)))
    audit.log(conn, actor, "place_hold", target_ref, "done", reason, basis)
    return cursor.lastrowid


def release_hold(conn, actor, hold_id, note):
    require(conn, actor, "release_hold", str(hold_id))
    hold = conn.execute("SELECT * FROM holds WHERE hold_id = ?", (hold_id,)).fetchone()
    if not hold or hold["placed_by"] == actor.name or not note.strip():
        refuse(conn, actor, "release_hold", str(hold_id), "SOD", "hold needs an independent person and a reason")
    conn.execute("UPDATE holds SET released_by = ?, released_at = ?, release_note = ? WHERE hold_id = ?",
                  (actor.name, clock.now(conn), note, hold_id))
    audit.log(conn, actor, "release_hold", str(hold_id), "done", note)


def entry_fingerprint(entry):
    lines = sorted((l.account, l.cost_center or "", l.event or "", l.item or "", l.person or "",
                    fmt(l.debit), fmt(l.credit)) for l in entry.lines)
    return hashlib.sha256(json.dumps([entry.entity, entry.period, entry.source_ref, lines]).encode()).hexdigest()


def stored_fingerprint(conn, entry_id):
    entry = conn.execute("SELECT entity,period,source_ref FROM journal_entries WHERE entry_id=?", (entry_id,)).fetchone()
    if not entry:
        raise ValueError("entry does not exist")
    lines = sorted((r["account"],r["cost_center"] or "",r["event"] or "",r["item"] or "",r["person"] or "",
                    r["debit"],r["credit"]) for r in conn.execute("SELECT * FROM journal_lines WHERE entry_id=?",(entry_id,)))
    return hashlib.sha256(json.dumps([entry[0],entry[1],entry[2],lines]).encode()).hexdigest()


def draft_entry(conn, actor, entry, case_id=None):
    require(conn, actor, "draft_entry", entry.entry_id)
    if entry.status != "draft" or entry.origin != "vouch":
        refuse(conn, actor, "draft_entry", entry.entry_id, "ROLE", "Vouch may only create its own drafts")
    if periods.status(conn, entry.period) == "locked":
        refuse(conn, actor, "draft_entry", entry.entry_id, "PERIOD", "book the correction in the current open period")
    # One live draft per original source/type. Distinct corrections use distinct source_ref values; a voided
    # draft no longer counts, so a reviewed and rejected treatment can be replaced by a different one.
    prior = conn.execute("SELECT entry_id FROM journal_entries WHERE source_ref = ? AND type = ? "
                          "AND origin = 'vouch' AND entry_id NOT IN (SELECT entry_id FROM entry_voids)",
                          (entry.source_ref, entry.type)).fetchone()
    if prior and not entry.corrects:
        refuse(conn, actor, "draft_entry", entry.entry_id, "DUPLICATE", f"source already drafted as {prior[0]}")
    post_entry(conn, entry)
    audit.log(conn, actor, "draft_entry", entry.entry_id, "done", entry.reason,
              {"case": case_id, "period": entry.period, "fingerprint": entry_fingerprint(entry),
               "sources": [{"table": s.table, "id": s.id} for l in entry.lines for s in l.sources]})
    return entry.entry_id


def voided(conn, entry_id):
    return conn.execute("SELECT 1 FROM entry_voids WHERE entry_id = ?", (entry_id,)).fetchone() is not None


def void_draft(conn, actor, entry_id, reason, basis=None):
    """Cancel a draft by appending a void (the books never delete). Its drafter may withdraw their own draft;
    Vouch may void its own draft once a reviewer rejected it. A posted entry is corrected, never voided."""
    entry = conn.execute("SELECT * FROM journal_entries WHERE entry_id = ?", (entry_id,)).fetchone()
    if entry is None or entry["status"] != "draft":
        refuse(conn, actor, "void_draft", entry_id, "LEDGER", "only an existing draft can be voided")
    if voided(conn, entry_id):
        return
    rejected = conn.execute("SELECT request_id, note FROM approval_requests WHERE kind='entry' AND subject_ref=? "
                            "AND status='rejected' ORDER BY closed_at DESC LIMIT 1", (entry_id,)).fetchone()
    require(conn, actor, "void_draft", entry_id)
    person = conn.execute("SELECT id FROM people WHERE id = ? OR name = ?", (actor.name, actor.name)).fetchone()
    own = entry["created_by"] in {actor.name, person[0] if person else None}
    if not ((actor.role == "vouch" and entry["created_by"] == VOUCH.name and rejected) or (actor.role != "vouch" and own)):
        refuse(conn, actor, "void_draft", entry_id, "SOD", "a draft is voided by its drafter, or by Vouch after a reviewer rejected it")
    if not reason.strip():
        refuse(conn, actor, "void_draft", entry_id, "LEDGER", "a void needs a reason")
    now = clock.now(conn)
    detail = {**(basis or {}), **({"rejected_request": rejected[0], "reviewer_note": rejected[1]} if rejected else {})}
    conn.execute("INSERT INTO entry_voids VALUES (?, ?, ?, ?, ?, ?)",
                 (entry_id, actor.name, actor.role, reason, json.dumps(detail, default=str), now))
    conn.execute("UPDATE approval_requests SET status = 'withdrawn', closed_at = ? WHERE kind = 'entry' AND "
                 "subject_ref = ? AND status IN ('pending', 'escalated')", (now, entry_id))
    audit.log(conn, actor, "void_draft", entry_id, "done", reason, {"drafted_by": entry["created_by"], **detail})


def post_approved_entry(conn, actor, entry_id):
    require(conn, actor, "post_entry", entry_id)
    if voided(conn, entry_id):
        refuse(conn, actor, "post_entry", entry_id, "LEDGER", "the draft was voided and can never be posted")
    request = conn.execute("SELECT * FROM approval_requests WHERE kind = 'entry' AND subject_ref = ? "
                           "ORDER BY created_at DESC LIMIT 1", (entry_id,)).fetchone()
    if request is None or request["status"] != "approved":
        refuse(conn, actor, "post_entry", entry_id, "APPROVAL", "entry has no approved request")
    if request["case_id"] and any(h["case_id"] == request["case_id"] for h in active_holds(conn)):
        refuse(conn, actor, "post_entry", entry_id, "HOLD", "the case is still held pending independent review")
    if json.loads(request["payload"]).get("entry_fingerprint") != stored_fingerprint(conn, entry_id):
        refuse(conn, actor, "post_entry", entry_id, "APPROVAL", "entry changed after approval was requested")
    entry = conn.execute("SELECT * FROM journal_entries WHERE entry_id = ?", (entry_id,)).fetchone()
    if not entry or periods.status(conn, entry["period"]) == "locked":
        refuse(conn, actor, "post_entry", entry_id, "PERIOD", "entry is absent or period is locked")
    if entry["status"] == "posted":
        return
    if entry["created_by"] == actor.name:
        refuse(conn, actor, "post_entry", entry_id, "SOD", "the drafter cannot post their own entry")
    conn.execute("UPDATE journal_entries SET status = 'posted' WHERE entry_id = ?", (entry_id,))
    audit.log(conn, actor, "post_entry", entry_id, "done", "posted after independent approval",
              {"request": request["request_id"]})
