"""Period close: closed months are locked; corrections go to the open month; only the
controller can lock or reopen a month."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from vouch import dates
from vouch.db.ledger import Entry, post_entry


@dataclass(frozen=True)
class Actor:
    name: str
    role: str        # vouch | controller | finance_manager | hr | treasury | stock_admin | department_head


class PeriodLocked(PermissionError):
    pass


class NotAuthorised(PermissionError):
    pass


def directory_actor(conn: sqlite3.Connection, role: str) -> Actor:
    """The named colleague holding a role, from the directory as loaded."""
    people = json.loads(conn.execute("SELECT content FROM documents WHERE kind = 'directory'").fetchone()[0])["people"]
    person = next(p for p in people if p["role"] == role)
    return Actor(person["name"], person["role"])


def status(conn: sqlite3.Connection, period: str) -> str:
    row = conn.execute("SELECT status FROM periods WHERE period = ?", (period,)).fetchone()
    if row is None:
        raise KeyError(f"unknown period {period}")
    return row[0]


def open_period(conn: sqlite3.Connection) -> str:
    rows = [r[0] for r in conn.execute("SELECT period FROM periods WHERE status = 'open' ORDER BY period")]
    if not rows:
        raise PeriodLocked("no open period")
    return rows[-1]


def _change(conn: sqlite3.Connection, period: str, action: str, actor: Actor, reason: str) -> None:
    if actor.role != "controller":
        raise NotAuthorised(f"{actor.name} ({actor.role}) cannot {action} a period; only the controller can")
    if not reason.strip():
        raise ValueError("a reason is required")
    target = "locked" if action == "lock" else "open"
    if status(conn, period) == target:
        return
    conn.execute("INSERT INTO period_events (period, action, actor, role, reason, at) VALUES (?, ?, ?, ?, ?, ?)",
                 (period, action, actor.name, actor.role, reason, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.execute("UPDATE periods SET status = ? WHERE period = ?", (target, period))


def lock_period(conn: sqlite3.Connection, period: str, actor: Actor, reason: str) -> None:
    _change(conn, period, "lock", actor, reason)


def reopen_period(conn: sqlite3.Connection, period: str, actor: Actor, reason: str) -> None:
    _change(conn, period, "reopen", actor, reason)


def post(conn: sqlite3.Connection, entry: Entry) -> None:
    """post_entry with the period check first, so a locked month gets a plain refusal."""
    if status(conn, entry.period) == "locked":
        raise PeriodLocked(f"{entry.period} is locked; book the correction in the open month "
                           f"{open_period(conn)} with corrects_period={entry.period}")
    post_entry(conn, entry)


def book_correction(conn: sqlite3.Connection, entry: Entry, corrects: str) -> Entry:
    """Book a correction to `corrects` in the open month. The entry date moves into the open month."""
    target = open_period(conn)
    day = entry.entry_date if dates.month_of(dates.parse_date(entry.entry_date)) == target \
        else dates.month_end(target).isoformat()
    corrected = replace(entry, period=target, entry_date=day,
                        corrects_period=corrects if corrects != target else None)
    post(conn, corrected)
    return corrected
