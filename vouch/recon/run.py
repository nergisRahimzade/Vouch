"""Runs every check over every loaded month for the open month's close and stores the findings."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from vouch.recon import materiality, rulings
from vouch.recon.checks import run_checks
from vouch.recon.findings import Findings
from vouch.recon.sources import Sources

SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    finding_id       TEXT NOT NULL,
    close_period     TEXT NOT NULL,         -- the open month whose close found it
    period           TEXT NOT NULL,         -- the month the fact belongs to
    check_name       TEXT NOT NULL,
    kind             TEXT NOT NULL,
    disposition      TEXT NOT NULL CHECK (disposition IN ('case', 'explained')),
    entity           TEXT,
    record           TEXT NOT NULL,         -- the whole finding as JSON
    created_at       TEXT NOT NULL,
    PRIMARY KEY (close_period, finding_id)
);
"""


def reconcile(conn: sqlite3.Connection) -> Findings:
    """Run the checks and store the result for the open month. Running twice replaces, never duplicates."""
    src = Sources(conn)
    rulings.store_from_documents(conn)
    findings = run_checks(src)
    materiality.apply(findings, src)
    rulings.attach(findings, rulings.past(conn, src.open_month))
    findings.validate(src.rule_ids)
    conn.executescript(SCHEMA)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM findings WHERE close_period = ?", (src.open_month,))
        conn.executemany(
            "INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(f.finding_id, src.open_month, f.period, f.check, f.kind, f.disposition, f.entity,
              json.dumps(f.to_record(), ensure_ascii=False), now) for f in findings])
    return findings


def stored(conn: sqlite3.Connection, close_period: str | None = None) -> list[dict]:
    query = "SELECT record FROM findings" + (" WHERE close_period = ?" if close_period else "")
    return [json.loads(r[0]) for r in conn.execute(query, (close_period,) if close_period else ())]
