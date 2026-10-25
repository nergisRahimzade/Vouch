"""Subledgers: accounts payable per vendor and prepaid event costs per event. Both roll up to the
general ledger; rollup() reports every place they do not."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from decimal import Decimal

from vouch.money import ZERO

UNASSIGNED = "(unassigned)"


def _balances(conn: sqlite3.Connection, account: str, key_sql: str, status: tuple[str, ...]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    marks = ",".join("?" for _ in status)
    rows = conn.execute(f"""SELECT {key_sql} AS key, l.debit, l.credit FROM journal_lines l
        JOIN journal_entries e USING (entry_id) LEFT JOIN invoices i ON i.id = e.source_ref
        WHERE l.account = ? AND e.status IN ({marks})""", (account, *status))
    for r in rows:
        out[r["key"] or UNASSIGNED] += Decimal(r["debit"]) - Decimal(r["credit"])
    return dict(out)


def ap_by_vendor(conn: sqlite3.Connection, status: tuple[str, ...] = ("posted",)) -> dict[str, Decimal]:
    """What is owed to each vendor (account 2000; a credit balance shows as negative)."""
    return _balances(conn, "2000", "i.vendor", status)


def prepaid_by_event(conn: sqlite3.Connection, status: tuple[str, ...] = ("posted",)) -> dict[str, Decimal]:
    """Deposits still held as prepaid for each event (account 1310)."""
    return _balances(conn, "1310", "l.event", status)


def gl_balance(conn: sqlite3.Connection, account: str, status: tuple[str, ...] = ("posted",)) -> Decimal:
    marks = ",".join("?" for _ in status)
    rows = conn.execute(f"SELECT l.debit, l.credit FROM journal_lines l JOIN journal_entries e USING (entry_id) "
                        f"WHERE l.account = ? AND e.status IN ({marks})", (account, *status))
    return sum((Decimal(r[0]) - Decimal(r[1]) for r in rows), ZERO)


def rollup(conn: sqlite3.Connection) -> list[str]:
    """Every difference between a subledger total and its general-ledger account."""
    out = []
    for account, ledger in (("2000", ap_by_vendor(conn)), ("1310", prepaid_by_event(conn))):
        if sum(ledger.values(), ZERO) != gl_balance(conn, account):
            out.append(f"{account}: subledger {sum(ledger.values(), ZERO)} vs general ledger {gl_balance(conn, account)}")
        if ledger.get(UNASSIGNED):
            out.append(f"{account}: {ledger[UNASSIGNED]} is not tied to a vendor or event")
    return out
