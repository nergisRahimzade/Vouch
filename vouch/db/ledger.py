"""Journal entries: dated, with a type and a reason, event and cost center on expense lines, linked to
the rules and rows they rest on, and balanced, or they are refused."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from vouch import dates
from vouch.db.accounts import BY_CODE
from vouch.money import ZERO, fmt

ENTRY_TYPES = {"staff_event", "hospitality", "gift", "promo", "working_meal", "program", "flat_tax_40",
               "flat_tax_37b", "vat_reversal", "reclass", "wages", "history"}


class EntryRefused(ValueError):
    def __init__(self, entry_id: str, reasons: list[str]):
        super().__init__(f"entry {entry_id} refused: " + "; ".join(reasons))
        self.entry_id, self.reasons = entry_id, reasons


class UnbalancedEntry(EntryRefused):
    pass


@dataclass(frozen=True)
class SourceRef:
    table: str          # a system-of-record table: rules, invoice_lines, attendance, invoices, ...
    id: str


@dataclass
class Line:
    account: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    cost_center: str | None = None
    event: str | None = None
    item: str | None = None             # the invoice, invoice line or gift the line books
    person: str | None = None           # the recipient or employee it concerns
    memo: str = ""
    sources: list[SourceRef] = field(default_factory=list)


@dataclass
class Entry:
    entry_id: str
    entity: str
    period: str
    entry_date: str
    type: str
    reason: str
    lines: list[Line]
    created_by: str
    origin: str = "vouch"
    status: str = "draft"
    source_ref: str | None = None
    corrects_period: str | None = None
    corrects: str | None = None

    def total(self) -> Decimal:
        return sum((Decimal(l.debit) for l in self.lines), ZERO)


def _is_amount(value) -> bool:
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError):
        return False
    return d >= 0 and d == d.quantize(Decimal("0.01"))


def problems(conn: sqlite3.Connection, entry: Entry) -> list[str]:
    entities = {r[0] for r in conn.execute("SELECT id FROM entities")}
    out = []
    if not entry.entry_id.strip():
        out.append("entry has no id")
    elif conn.execute("SELECT 1 FROM journal_entries WHERE entry_id = ?", (entry.entry_id,)).fetchone():
        out.append("an entry with this id already exists")
    if entry.entity not in entities:
        out.append(f"unknown entity {entry.entity!r}")
    if not conn.execute("SELECT 1 FROM periods WHERE period = ?", (entry.period,)).fetchone():
        out.append(f"unknown period {entry.period!r}")
    try:
        if dates.month_of(dates.parse_date(entry.entry_date)) != entry.period:
            out.append(f"date {entry.entry_date} is outside period {entry.period}")
    except (ValueError, TypeError, AttributeError):
        out.append(f"invalid date {entry.entry_date!r}")
    if entry.type not in ENTRY_TYPES:
        out.append(f"unknown entry type {entry.type!r}")
    if not entry.reason.strip():
        out.append("entry has no reason")
    if entry.status not in ("draft", "posted"):
        out.append(f"unknown status {entry.status!r}")
    if len(entry.lines) < 2:
        out.append("an entry needs at least two lines")
    for n, line in enumerate(entry.lines, 1):
        account = BY_CODE.get(line.account)
        if account is None:
            out.append(f"line {n}: account {line.account!r} is not in the chart of accounts")
        if not (_is_amount(line.debit) and _is_amount(line.credit)):
            out.append(f"line {n}: amounts must be non-negative with at most two decimals")
        elif (Decimal(line.debit) > 0) == (Decimal(line.credit) > 0):
            out.append(f"line {n}: exactly one of debit or credit must be positive")
        if account and account.needs_tags and entry.origin == "vouch" and not (line.cost_center and
                                                                                (line.event or line.item)):
            out.append(f"line {n}: an expense line needs a cost center and the event (or item) it books "
                       f"(got {line.cost_center!r}, {line.event!r}, {line.item!r})")
        if entry.origin == "vouch" and not line.sources:
            out.append(f"line {n}: no source rows")
        for source in line.sources:
            if source.table not in SOURCE_TABLES:
                out.append(f"line {n}: {source.table} is not a system-of-record table")
                continue
            key = SOURCE_TABLES[source.table]
            if not conn.execute(f"SELECT 1 FROM {source.table} WHERE {key} = ?", (source.id,)).fetchone():
                out.append(f"line {n}: source {source.table}:{source.id} does not exist")
    return out


# Tables a line may rest on, with their key column.
SOURCE_TABLES = {"invoices": "id", "invoice_lines": "id", "rules": "rule_id", "policies": "id", "events": "id",
                 "attendance": "id", "gift_distributions": "id", "payroll_exports": "id", "journal_entries": "entry_id",
                 "emails": "id", "business_partners": "id", "employees": "id",
                 "received_facts": "request_id", "followup_facts": "id", "learned_corrections": "id"}


def balance(entry: Entry) -> Decimal:
    return sum((Decimal(l.debit) for l in entry.lines), ZERO) - sum((Decimal(l.credit) for l in entry.lines), ZERO)


def currency_of(conn: sqlite3.Connection, entity: str) -> str:
    return conn.execute("SELECT currency FROM entities WHERE id = ?", (entity,)).fetchone()[0]


def post_entry(conn: sqlite3.Connection, entry: Entry) -> None:
    """Validate and write one entry. Refused entries write nothing."""
    reasons = problems(conn, entry)
    difference = balance(entry) if not any("amounts must be" in r for r in reasons) else ZERO
    if difference:
        raise UnbalancedEntry(entry.entry_id, reasons + [f"debits and credits differ by {fmt(difference)}"])
    if reasons:
        raise EntryRefused(entry.entry_id, reasons)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not conn.in_transaction:
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT post_entry")      # all or nothing; the caller decides when to commit
    try:
        conn.execute(
            "INSERT INTO journal_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.entry_id, entry.entity, entry.period, entry.entry_date, entry.type, entry.reason,
             currency_of(conn, entry.entity), entry.status, entry.origin, entry.created_by, entry.source_ref,
             entry.corrects_period, entry.corrects, created))
        for n, line in enumerate(entry.lines, 1):
            conn.execute("INSERT INTO journal_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (entry.entry_id, n, line.account, line.cost_center, line.event, line.item, line.person,
                          fmt(line.debit), fmt(line.credit), line.memo))
            conn.executemany("INSERT INTO line_sources VALUES (?, ?, ?, ?)",
                             [(entry.entry_id, n, s.table, s.id) for s in line.sources])
    except Exception:
        conn.execute("ROLLBACK TO post_entry")
        conn.execute("RELEASE post_entry")
        raise
    conn.execute("RELEASE post_entry")


def entry_lines(conn: sqlite3.Connection, entry_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM journal_lines WHERE entry_id = ? ORDER BY line_no",
                                          (entry_id,))]
