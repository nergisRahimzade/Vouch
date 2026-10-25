"""Period close: month M closes on the 10th of M+1 (the monthly VAT return deadline) and is then locked; something that belongs to a
closed month is booked in the current open period; only the controller can lock or reopen a month."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone

from vouch import dates
from vouch.db.ledger import Entry, post_entry

CLOSE_DAY = 10         # month M closes on the 10th of M+1 (spec §5.7)


@dataclass(frozen=True)
class Actor:
    name: str
    role: str        # vouch | tax_manager | controller | payroll | cfo | organizer | vendor


class PeriodLocked(PermissionError):
    pass


class NotAuthorised(PermissionError):
    pass


def close_date(period: str) -> date:
    return dates.month_start(dates.add_months(period, 1)).replace(day=CLOSE_DAY)


def closed_at(period: str, as_of: date) -> bool:
    """A month is closed once its close date has passed: on the 10th itself it can still take entries."""
    return as_of > close_date(period)


def as_of(conn: sqlite3.Connection) -> date:
    return date.fromisoformat(conn.execute("SELECT value FROM scenario WHERE key = 'as_of'").fetchone()[0])


def person_actor(conn: sqlite3.Connection, role: str) -> Actor:
    """The named person holding a role."""
    row = conn.execute("SELECT name, role FROM people WHERE role = ? ORDER BY id", (role,)).fetchone()
    if row is None:
        raise KeyError(f"no one holds the role {role}")
    return Actor(row[0], row[1])


def status(conn: sqlite3.Connection, period: str) -> str:
    row = conn.execute("SELECT status FROM periods WHERE period = ?", (period,)).fetchone()
    if row is None:
        raise KeyError(f"unknown period {period}")
    return row[0]


def current_period(conn: sqlite3.Connection) -> str:
    """The period of the as_of date: always open, because its close date is in the next month."""
    return dates.month_of(as_of(conn))


def open_periods(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT period FROM periods WHERE status = 'open' ORDER BY period")]


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
        raise PeriodLocked(f"{entry.period} is closed; book it in the current open period {current_period(conn)} "
                           f"with corrects_period={entry.period}")
    post_entry(conn, entry)


def into_open_period(conn: sqlite3.Connection, entry: Entry) -> Entry:
    """The entry as it must be booked: unchanged when its period is open; otherwise moved to the
    as_of date in the current period, naming the closed period it belongs to."""
    if status(conn, entry.period) == "open":
        return entry
    day = as_of(conn)
    return replace(entry, period=dates.month_of(day), entry_date=day.isoformat(), corrects_period=entry.period)
