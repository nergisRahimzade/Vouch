"""Simulated clock. The close runs on its own time so reminders and escalations can be tested."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from vouch import dates


def start_of_close(conn: sqlite3.Connection) -> datetime:
    """Start at 09:00 on the scenario date, never the host's wall clock."""
    day = conn.execute("SELECT value FROM scenario WHERE key = 'as_of'").fetchone()[0]
    return datetime.fromisoformat(day[:10] + "T09:00:00")


def now(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT now FROM clock WHERE id = 1").fetchone()
    if row is None:
        start = start_of_close(conn).isoformat()
        conn.execute("INSERT INTO clock (id, now) VALUES (1, ?)", (start,))
        return start
    return row[0]


def advance(conn: sqlite3.Connection, **delta) -> str:
    moved = (datetime.fromisoformat(now(conn)) + timedelta(**delta)).isoformat()
    conn.execute("UPDATE clock SET now = ? WHERE id = 1", (moved,))
    return moved


def business_days_between(start: str, end: str) -> int:
    a, b = datetime.fromisoformat(start).date(), datetime.fromisoformat(end).date()
    days, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days
