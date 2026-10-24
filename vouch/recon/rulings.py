"""Past rulings: stored once, reused by every later close. A finding cites each earlier ruling
about its kind of problem, so a decision made once is respected the next time."""
from __future__ import annotations

import json
import sqlite3

from vouch.recon.findings import Finding, Findings

SCHEMA = """
CREATE TABLE IF NOT EXISTS rulings (
    ruling_id            TEXT PRIMARY KEY,
    period               TEXT NOT NULL,
    decided_on           TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    subject_employee_id  TEXT,
    decision             TEXT NOT NULL,
    decided_by           TEXT NOT NULL,
    rules_cited          TEXT NOT NULL,     -- JSON list
    precedent            TEXT NOT NULL,
    source               TEXT NOT NULL      -- where the ruling came from
);
"""

# Which ruling kinds speak to which kind of finding, and when. A ruling recorded later
# under a finding's own kind always applies to that kind.
APPLIES_TO = {
    "bank_details_differ_from_hr": [("bank_change_by_email", lambda f: "email_id" in f.subject)],
    "fx_translation_differs": [("fx_difference_below_materiality", lambda f: True)],
    "backdated_salary_change": [("backdated_salary_change", lambda f: True)],
}


def store_from_documents(conn: sqlite3.Connection) -> int:
    """Copy rulings delivered as documents into the rulings table (once; later runs keep them)."""
    conn.executescript(SCHEMA)
    added = 0
    for doc_id, path, row_no, data in conn.execute(
            """SELECT d.doc_id, d.path, r.row_no, r.data FROM documents d JOIN source_rows r USING (doc_id)
               WHERE d.kind = 'rulings'""").fetchall():
        ruling = json.loads(data)
        cursor = conn.execute(
            "INSERT OR IGNORE INTO rulings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ruling["ruling_id"], ruling["period"], ruling["decided_on"], ruling["kind"],
             ruling.get("subject_employee_id") or None, ruling["decision"], ruling["decided_by"],
             json.dumps(ruling["rules_cited"]), ruling["precedent"], f"{path}#{row_no}"))
        added += cursor.rowcount
    conn.commit()
    return added


def past(conn: sqlite3.Connection, before: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM rulings WHERE period < ? ORDER BY decided_on, ruling_id", (before,))
    return [dict(r) for r in rows]


def applicable(finding: Finding, rulings: list[dict]) -> list[dict]:
    kinds = [(finding.kind, lambda f: True)] + APPLIES_TO.get(finding.kind, [])
    return [r for r in rulings for kind, when in kinds if r["kind"] == kind and when(finding)]


def attach(findings: Findings, rulings: list[dict]) -> None:
    for finding in findings:
        for ruling in applicable(finding, rulings):
            cited = {"ruling_id": ruling["ruling_id"], "decision": ruling["decision"],
                     "decided_by": ruling["decided_by"], "period": ruling["period"], "precedent": ruling["precedent"]}
            if cited not in finding.precedents:
                finding.precedents.append(cited)
