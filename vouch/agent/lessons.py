"""Lessons: how Vouch gets better from one close to the next without anyone retraining a model.

After a live close, code (not the model, so it costs nothing) looks at the agent's own dead ends in that run:
tool calls that were refused or failed because of how the agent asked (a promise it could not keep, a hold on
something outside the case, a request for a draft that did not exist). Each becomes a proposed lesson about a
KIND of case, with every identifier and name taken out, so no lesson can single out a person, payment or grant.

A lesson does nothing until the controller approves it (Vouch cannot approve its own). Approved lessons are kept
in a file under version control, so every close and every score uses exactly the same set, and each score names
the set it ran with. Lessons are learned only from the main set; runs on any other company never feed them,
so a score on a set kept apart for testing stays honest."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from vouch.controls import audit, clock, gates
from vouch.db.periods import Actor

APPROVED_FILE = Path(__file__).resolve().parents[2] / "lessons" / "approved.json"
LEARN_FROM_SET = "main"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    lesson_id    TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    text         TEXT NOT NULL,
    evidence     TEXT NOT NULL,          -- JSON: audit rows it was drawn from
    from_run     TEXT NOT NULL,
    status       TEXT NOT NULL,          -- proposed | approved | rejected
    proposed_at  TEXT NOT NULL,
    decided_by   TEXT,
    decided_at   TEXT,
    note         TEXT
);
CREATE TABLE IF NOT EXISTS world_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

RULE_ID = re.compile(r"^(?:PAY|FIN)-\d{2}$")
IDENTIFIER = re.compile(r"\b[A-Z]{1,6}(?:-[A-Za-z0-9]+)*-[A-Za-z0-9]*\d[A-Za-z0-9]*\b")
LONG_NUMBER = re.compile(r"\b[A-Z]{0,2}\d[\d:\- ]{5,}\d\b")
# Refusals that say something about how the agent asked, not about the world (a broken tool is not a lesson).
AGENT_MISTAKES = ("BadQuestion", "CannotDraft", "is not part of case", "draft it first", "only this case's",
                  "cannot promise", "is not resolved by Vouch")


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def set_world(conn: sqlite3.Connection, set_name: str) -> None:
    """Record which company set the database was built from (the dashboard's rebuild calls this)."""
    ensure(conn)
    conn.execute("INSERT OR REPLACE INTO world_meta VALUES ('set', ?)", (set_name,))
    conn.commit()


def world_set(conn: sqlite3.Connection) -> str:
    """The set this database was built from. A database built without saying is the main set (the CLI's
    `python -m vouch rebuild` builds only the main set)."""
    ensure(conn)
    row = conn.execute("SELECT value FROM world_meta WHERE key = 'set'").fetchone()
    return row[0] if row else LEARN_FROM_SET


def scrub(text: str, names: list[str]) -> str:
    """Take every identifier, account number and person's name out, keeping rule IDs (PAY-xx, FIN-xx)."""
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name, "<person>")
    text = IDENTIFIER.sub(lambda m: m.group(0) if RULE_ID.match(m.group(0)) else "<id>", text)
    return LONG_NUMBER.sub("<number>", text)


def _lesson_text(kind: str, tool: str, reason: str) -> str:
    return (f"On {kind.replace('_', ' ')} cases, an earlier close called {tool} and was refused: {reason}. "
            "Avoid that step on this kind of case; use a step the tools accept.")


def propose(conn: sqlite3.Connection) -> list[dict]:
    """Proposed lessons from the latest live close's own dead ends. Free: code reads the audit trail."""
    ensure(conn)
    current = world_set(conn)
    if current != LEARN_FROM_SET:
        raise gates.Refused("LESSONS", f"lessons are learned only from the {LEARN_FROM_SET} set; this company is "
                                       f"the {current} set")
    run = conn.execute("SELECT run_id, mode FROM agent_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    if run is None or run["mode"] != "model":
        raise gates.Refused("LESSONS", "lessons come from a live close (the scripted playbook teaches nothing)")
    start = conn.execute("SELECT MAX(seq) FROM audit_log WHERE action = 'close_started'").fetchone()[0]
    kinds = {r[0]: r[1] for r in conn.execute("SELECT case_id, kind FROM cases")}
    names = [p["name"] for p in json.loads(conn.execute(
        "SELECT content FROM documents WHERE kind = 'directory'").fetchone()[0])["people"]]
    names += [json.loads(r[0]).get("employee_name", "") for r in conn.execute(
        "SELECT data FROM source_rows r JOIN documents d USING (doc_id) WHERE d.kind = 'hr_census'")]
    names = [n for n in set(names) if n]
    proposed, now = {}, clock.now(conn)
    for row in conn.execute("SELECT seq, action, target, reason, basis FROM audit_log WHERE seq > ? AND "
                            "action LIKE 'tool:%' AND outcome IN ('failed', 'refused') ORDER BY seq", (start,)):
        basis = json.loads(row["basis"])
        if basis.get("prefetched") or not any(m in row["reason"] for m in AGENT_MISTAKES):
            continue
        case_id = basis.get("input", {}).get("case_id") or row["target"]
        kind = kinds.get(case_id)
        if not kind:
            continue
        text = _lesson_text(kind, row["action"][5:], scrub(row["reason"], names))
        lesson_id = "LES-" + hashlib.sha1(f"{kind}|{text}".encode()).hexdigest()[:10]
        item = proposed.setdefault(lesson_id, {"lesson_id": lesson_id, "kind": kind, "text": text, "evidence": []})
        item["evidence"].append(row["seq"])
    out = []
    for item in proposed.values():
        cursor = conn.execute("INSERT OR IGNORE INTO lessons VALUES (?, ?, ?, ?, ?, 'proposed', ?, NULL, NULL, NULL)",
                              (item["lesson_id"], item["kind"], item["text"], json.dumps(item["evidence"]),
                               run["run_id"], now))
        if cursor.rowcount:
            audit.log(conn, gates.VOUCH, "propose_lesson", item["lesson_id"], "requested", item["text"],
                      {"kind": item["kind"], "evidence_rows": item["evidence"], "run": run["run_id"]})
            out.append(item)
    conn.commit()
    return out


def decide(conn: sqlite3.Connection, actor: Actor, lesson_id: str, decision: str, note: str,
           approved_file: Path | None = None) -> dict:
    """Only the controller approves or rejects a lesson, with a note. An approved lesson joins the file every
    later close reads."""
    ensure(conn)
    approved_file = approved_file or APPROVED_FILE
    row = conn.execute("SELECT * FROM lessons WHERE lesson_id = ?", (lesson_id,)).fetchone()
    if row is None:
        raise KeyError(lesson_id)
    if actor.role != "controller":
        gates.refuse(conn, actor, "decide_lesson", lesson_id, "ROLE",
                     f"only the controller approves lessons, not the {actor.role}")
    if row["status"] != "proposed":
        gates.refuse(conn, actor, "decide_lesson", lesson_id, "FIN-08", f"the lesson is already {row['status']}")
    if decision not in ("approve", "reject"):
        raise ValueError("a decision is approve or reject")
    if not note.strip():
        gates.refuse(conn, actor, "decide_lesson", lesson_id, "FIN-04", "a decision needs a note")
    status, now = ("approved" if decision == "approve" else "rejected"), clock.now(conn)
    conn.execute("UPDATE lessons SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE lesson_id = ?",
                 (status, actor.name, now, note, lesson_id))
    if status == "approved":
        approved = load_all(approved_file)
        if not any(a["lesson_id"] == lesson_id for a in approved):
            approved.append({"lesson_id": lesson_id, "kind": row["kind"], "text": row["text"],
                             "approved_by": actor.name, "approved_at": now, "note": note})
            approved_file.parent.mkdir(parents=True, exist_ok=True)
            approved_file.write_text(json.dumps(approved, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    audit.log(conn, actor, f"{decision}_lesson", lesson_id, "done", note, {"kind": row["kind"], "text": row["text"]})
    conn.commit()
    return {"lesson_id": lesson_id, "status": status, "by": actor.name}


def load_all(approved_file: Path | None = None) -> list[dict]:
    approved_file = approved_file or APPROVED_FILE
    return json.loads(approved_file.read_text(encoding="utf-8")) if approved_file.exists() else []


def for_kind(kind: str, approved_file: Path | None = None) -> list[str]:
    return [a["text"] for a in load_all(approved_file) if a["kind"] == kind]


def fingerprint(approved_file: Path | None = None) -> dict:
    """Which lessons a run used: how many, and a hash of the file, for every score's label."""
    approved_file = approved_file or APPROVED_FILE
    data = approved_file.read_bytes() if approved_file.exists() else b"[]"
    return {"count": len(load_all(approved_file)), "sha256": hashlib.sha256(data).hexdigest()[:12]}
