"""Journal entries: dated, with a reason, entity and department on expense lines, linked to
their source rows, and balanced, or they are refused."""
from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from vouch import dates
from vouch.db.accounts import BY_CODE
from vouch.money import ZERO, fmt


class EntryRefused(ValueError):
    def __init__(self, entry_id: str, reasons: list[str]):
        super().__init__(f"entry {entry_id} refused: " + "; ".join(reasons))
        self.entry_id, self.reasons = entry_id, reasons


class UnbalancedEntry(EntryRefused):
    pass


@dataclass(frozen=True)
class SourceRef:
    doc_id: int
    row_no: int | None = None        # None: the whole document
    role: str = "evidence"


@dataclass
class Line:
    account: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    department: str | None = None
    memo: str = ""
    fx_rate: str | None = None
    sources: list[SourceRef] = field(default_factory=list)


@dataclass
class Entry:
    entry_id: str
    entity: str
    period: str
    entry_date: str
    reason: str
    lines: list[Line]
    created_by: str
    origin: str = "vouch"
    status: str = "draft"
    corrects_period: str | None = None


def _company(conn: sqlite3.Connection) -> dict:
    return json.loads(conn.execute("SELECT content FROM documents WHERE kind = 'company'").fetchone()[0])


def _is_amount(value) -> bool:
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError):
        return False
    return d >= 0 and d == d.quantize(Decimal("0.01"))


def problems(conn: sqlite3.Connection, entry: Entry) -> list[str]:
    company = _company(conn)
    currencies = company["functional_currency_by_entity"]
    out = []
    if not entry.entry_id.strip():
        out.append("entry has no id")
    elif conn.execute("SELECT 1 FROM journal_entries WHERE entry_id = ?", (entry.entry_id,)).fetchone():
        out.append("an entry with this id already exists")
    if entry.entity not in currencies:
        out.append(f"unknown entity {entry.entity!r}")
    if not conn.execute("SELECT 1 FROM periods WHERE period = ?", (entry.period,)).fetchone():
        out.append(f"unknown period {entry.period!r}")
    try:
        if dates.month_of(dates.parse_date(entry.entry_date)) != entry.period:
            out.append(f"date {entry.entry_date} is outside period {entry.period}")
    except (ValueError, TypeError, AttributeError):
        out.append(f"invalid date {entry.entry_date!r}")
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
        elif (line.debit > 0) == (line.credit > 0):
            out.append(f"line {n}: exactly one of debit or credit must be positive")
        if account and account.needs_department and line.department not in company["departments"]:
            out.append(f"line {n}: expense line needs a department (got {line.department!r})")
        if not any(s.role == "evidence" for s in line.sources):
            out.append(f"line {n}: no source rows")
        for source in line.sources:
            if not conn.execute("SELECT 1 FROM documents WHERE doc_id = ?", (source.doc_id,)).fetchone():
                out.append(f"line {n}: source document {source.doc_id} does not exist")
            elif source.row_no is not None and not conn.execute(
                    "SELECT 1 FROM source_rows WHERE doc_id = ? AND row_no = ?", (source.doc_id, source.row_no)).fetchone():
                out.append(f"line {n}: source row {source.doc_id}:{source.row_no} does not exist")
    return out


def balance(entry: Entry) -> Decimal:
    return sum((Decimal(l.debit) for l in entry.lines), ZERO) - sum((Decimal(l.credit) for l in entry.lines), ZERO)


def post_entry(conn: sqlite3.Connection, entry: Entry) -> None:
    """Validate and write one entry. Refused entries write nothing."""
    reasons = problems(conn, entry)
    difference = balance(entry) if not any("amounts must be" in r for r in reasons) else ZERO
    if difference:
        raise UnbalancedEntry(entry.entry_id, reasons + [f"debits and credits differ by {fmt(difference)}"])
    if reasons:
        raise EntryRefused(entry.entry_id, reasons)
    currency = _company(conn)["functional_currency_by_entity"][entry.entity]
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not conn.in_transaction:
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT post_entry")      # all or nothing; the caller decides when to commit
    try:
        conn.execute(
            "INSERT INTO journal_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.entry_id, entry.entity, entry.period, entry.entry_date, entry.reason, currency, entry.status,
             entry.origin, entry.created_by, entry.corrects_period, created))
        for n, line in enumerate(entry.lines, 1):
            conn.execute("INSERT INTO journal_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (entry.entry_id, n, line.account, line.department, fmt(line.debit), fmt(line.credit),
                          line.fx_rate, line.memo))
            conn.executemany("INSERT INTO line_sources VALUES (?, ?, ?, ?, ?)",
                             [(entry.entry_id, n, s.role, s.doc_id, s.row_no) for s in line.sources])
    except Exception:
        conn.execute("ROLLBACK TO post_entry")
        conn.execute("RELEASE post_entry")
        raise
    conn.execute("RELEASE post_entry")


# -- loading the ledger as already booked -------------------------------------
class _Resolver:
    """Turns a booked line's source_ref into source rows."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.docs = {r["path"]: r["doc_id"] for r in conn.execute("SELECT path, doc_id FROM documents")}
        self.by_ref = {}
        for view, column in (("v_bank_statement", "bank_line_id"), ("v_payment_file", "payment_id")):
            for r in conn.execute(f"SELECT doc_id, row_no, {column} AS ref FROM {view}"):
                self.by_ref[r["ref"]] = (r["doc_id"], r["row_no"])

    def resolve(self, ref: str) -> list[SourceRef]:
        path, _, selector = ref.partition("#")
        if path in self.docs:
            doc_id = self.docs[path]
            if not selector:
                return [SourceRef(doc_id)]
            conditions = [part.partition("=") for part in selector.split(";")]
            where = " AND ".join("json_extract(data, '$.' || ?) = ?" for _ in conditions)
            params = [v for key, _, value in conditions for v in (key, value)]
            rows = self.conn.execute(f"SELECT row_no FROM source_rows WHERE doc_id = ? AND {where} ORDER BY row_no",
                                     (doc_id, *params))
            return [SourceRef(doc_id, r[0]) for r in rows]
        if ref in self.by_ref:
            return [SourceRef(*self.by_ref[ref])]
        return []


def load_booked_ledger(conn: sqlite3.Connection) -> dict:
    """Bring each month's ledger-as-booked into the general ledger as posted history."""
    resolver = _Resolver(conn)
    posted, refused = 0, []
    documents = conn.execute("SELECT doc_id, path FROM documents WHERE kind = 'ledger_booked' ORDER BY month").fetchall()
    for doc in documents:
        entries: OrderedDict[str, list] = OrderedDict()
        for row in conn.execute("SELECT row_no, data FROM source_rows WHERE doc_id = ? ORDER BY row_no", (doc["doc_id"],)):
            line = json.loads(row["data"])
            entries.setdefault(line["entry_id"], []).append((row["row_no"], line))
        for entry_id, rows in entries.items():
            first = rows[0][1]
            entry = Entry(
                entry_id=entry_id, entity=first["entity"], period=first["period"], entry_date=first["date"],
                reason=first["memo"], created_by=first["booked_by"], origin="booked_ledger", status="posted",
                lines=[Line(account=l["account"], debit=Decimal(l["debit"]), credit=Decimal(l["credit"]),
                            department=l["department"] or None, memo=l["memo"], fx_rate=l["fx_rate"] or None,
                            sources=resolver.resolve(l["source_ref"]) + [SourceRef(doc["doc_id"], n, "booked_as")])
                       for n, l in rows])
            try:
                post_entry(conn, entry)
                posted += 1
            except EntryRefused as refusal:
                refused.append(entry_id)
                conn.execute("INSERT INTO refused_entries VALUES (?, ?, ?, ?)",
                             (entry_id, entry.period, json.dumps(refusal.reasons), doc["path"]))
    return {"posted": posted, "refused": refused}
