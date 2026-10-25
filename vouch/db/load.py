"""Loads the company world (as it stands on as_of) into the system of record. After this step
everything reads the database; nothing reads the fixtures or the scenario file."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

from vouch import dates
from vouch.db import periods
from vouch.db.ledger import Entry, EntryRefused, Line, post_entry
from vouch.world.scenario import TABLES, to_text

LEDGER_TABLES = {"journal_entries", "journal_lines"}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """Insert rows as given; fields the table has no column for are kept in `extra`."""
    columns = _columns(conn, table)
    for row in rows:
        known = {k: v for k, v in row.items() if k in columns and k != "extra"}
        extra = {k: v for k, v in row.items() if k not in columns}
        if "extra" in columns and extra:
            known["extra"] = extra
        names = list(known)
        conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                     [to_text(known[n]) for n in names])
    return len(rows)


def _period_range(rows: dict, meta: dict) -> list[str]:
    """Every month from the first period (or the earliest history entry) to two months after as_of,
    so a close can date its automatic reversal on the 1st of the next month."""
    months = [meta["first_period"]] + [str(e["entry_date"])[:7] for e in rows["journal_entries"]]
    last = dates.add_months(meta["as_of"][:7], 2)
    return dates.month_range(min(months), last)


def _history_entry(conn: sqlite3.Connection, row: dict, line_rows: list[dict]) -> Entry:
    """A journal entry the company booked before Vouch looked (fixtures or a scenario's setup)."""
    lines = []
    invoice = conn.execute("SELECT event, entity FROM invoices WHERE id = ?", (row.get("source_ref"),)).fetchone()
    for raw in (row.get("lines") or []) + line_rows:
        event = raw.get("event") or row.get("event") or (invoice["event"] if invoice else None)
        lines.append(Line(account=str(raw["account"]), debit=Decimal(str(raw.get("debit") or "0")),
                          credit=Decimal(str(raw.get("credit") or "0")), cost_center=raw.get("cost_center"),
                          event=event, item=raw.get("item"), person=raw.get("person"), memo=str(raw.get("memo") or "")))
    entity = row.get("entity") or (invoice["entity"] if invoice else None) or \
        conn.execute("SELECT id FROM entities ORDER BY id").fetchone()[0]
    day = str(row["entry_date"])[:10]
    return Entry(entry_id=str(row["entry_id"]), entity=str(entity), period=day[:7], entry_date=day,
                 type=str(row.get("type") or "history"), reason=str(row.get("reason") or "booked before Vouch"),
                 lines=lines, created_by=str(row.get("booked_by") or "history"), origin="history", status="posted",
                 source_ref=row.get("source_ref"), corrects=row.get("corrects"))


def load(conn: sqlite3.Connection, rows: dict, meta: dict) -> dict:
    """Load every table, book the history into the ledger, then lock the months already closed on as_of."""
    conn.execute("INSERT INTO scenario VALUES ('loading', '1')")
    for key in ("as_of", "scenario_id", "title"):
        conn.execute("INSERT INTO scenario VALUES (?, ?)", (key, meta[key]))
    for period in _period_range(rows, meta):
        conn.execute("INSERT INTO periods VALUES (?, ?, 'open')", (period, periods.close_date(period).isoformat()))
    counts = {t: insert_rows(conn, t, rows[t]) for t in TABLES if t not in LEDGER_TABLES}
    lines_by_entry: dict[str, list[dict]] = {}
    for line in rows["journal_lines"]:
        lines_by_entry.setdefault(str(line["entry_id"]), []).append(line)
    posted, refused = 0, []
    for row in rows["journal_entries"]:
        entry = _history_entry(conn, row, lines_by_entry.get(str(row["entry_id"]), []))
        try:
            post_entry(conn, entry)
            posted += 1
        except EntryRefused as refusal:
            refused.append(entry.entry_id)
            conn.execute("INSERT INTO refused_entries VALUES (?, ?, ?, ?)",
                         (entry.entry_id, entry.period, json.dumps(refusal.reasons), "history"))
    controller = periods.person_actor(conn, "controller")
    as_of = periods.as_of(conn)
    for (period,) in conn.execute("SELECT period FROM periods ORDER BY period").fetchall():
        if periods.closed_at(period, as_of):
            periods.lock_period(conn, period, controller, f"closed on {periods.close_date(period)}")
    conn.execute("DELETE FROM scenario WHERE key = 'loading'")
    return {"rows": counts, "entries_posted": posted, "entries_refused": refused}


def summary(conn: sqlite3.Connection) -> dict:
    status = {r[0]: r[1] for r in conn.execute("SELECT period, status FROM periods ORDER BY period")}
    return {"as_of": periods.as_of(conn).isoformat(),
            "scenario": conn.execute("SELECT value FROM scenario WHERE key = 'scenario_id'").fetchone()[0],
            "locked": [p for p, s in status.items() if s == "locked"],
            "open": [p for p, s in status.items() if s == "open"]}
