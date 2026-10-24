"""Subledgers: payroll per person per period, equity per grant per month. Both roll up to
the general ledger; rollup() reports every place they do not."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from vouch.money import ZERO, cents, fmt

SCHEMA = """
CREATE TABLE payroll_subledger (
    doc_id INTEGER NOT NULL, row_no INTEGER NOT NULL,
    period TEXT NOT NULL, entity TEXT NOT NULL, employee_id TEXT NOT NULL, department TEXT NOT NULL,
    currency TEXT NOT NULL, gross TEXT NOT NULL, income_tax TEXT NOT NULL, employee_social TEXT NOT NULL,
    net TEXT NOT NULL, employer_social TEXT NOT NULL, employer_pension TEXT NOT NULL,
    PRIMARY KEY (doc_id, row_no), FOREIGN KEY (doc_id, row_no) REFERENCES source_rows(doc_id, row_no)
);
CREATE TABLE equity_subledger (
    doc_id INTEGER NOT NULL, row_no INTEGER NOT NULL,
    period TEXT NOT NULL, entity TEXT NOT NULL, grant_id TEXT NOT NULL, employee_id TEXT NOT NULL,
    department TEXT NOT NULL, status TEXT NOT NULL, expense_usd TEXT NOT NULL,
    fx_rate TEXT, fx_basis TEXT, expense_local TEXT,   -- NULL until the month's stock entry fixes the rate
    PRIMARY KEY (doc_id, row_no), FOREIGN KEY (doc_id, row_no) REFERENCES source_rows(doc_id, row_no)
);
"""

# How each register column posts. Every amount column of a register must be listed here.
REGISTER_COLUMNS = {
    "income_tax": ["federal_income_tax", "paye_income_tax", "lohnsteuer", "solidaritaetszuschlag"],
    "employee_social": ["employee_social_security", "employee_medicare", "employee_additional_medicare",
                        "employee_ni", "employee_rv", "employee_av", "employee_kv", "employee_pv"],
    "employer_social": ["employer_social_security", "employer_medicare", "employer_futa", "employer_ni",
                        "employer_rv", "employer_av", "employer_kv", "employer_pv"],
    "employer_pension": ["employer_pension"],
}
NOT_POSTED = {"annual_salary", "gross_pay", "net_pay", "employer_pension_rate"}


class UnmappedColumn(ValueError):
    """A register carries an amount the subledger does not know how to post. Never dropped."""


def _register_amounts(row: dict) -> dict[str, Decimal]:
    known = {c for columns in REGISTER_COLUMNS.values() for c in columns}
    for column in row:
        if column.startswith(("employee_", "employer_")) and column not in known | NOT_POSTED \
                and column not in ("employee_id", "employee_name"):
            raise UnmappedColumn(f"register column {column!r} has no posting rule")
    return {kind: sum((Decimal(row[c]) for c in columns if row.get(c)), ZERO)
            for kind, columns in REGISTER_COLUMNS.items()}


def stock_rates(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str]]:
    """(period, entity) -> (rate, basis) the month's posted stock compensation used."""
    rows = conn.execute("""SELECT DISTINCT e.period, e.entity, l.fx_rate, l.fx_basis FROM journal_lines l
        JOIN journal_entries e USING (entry_id) WHERE l.account = '6300' AND e.status = 'posted'""")
    return {(r["period"], r["entity"]): (r["fx_rate"], r["fx_basis"]) for r in rows}


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def build(conn: sqlite3.Connection) -> dict:
    payroll = 0
    for r in conn.execute("""SELECT d.doc_id, r.row_no, d.month, d.entity, r.data FROM documents d
            JOIN source_rows r USING (doc_id) WHERE d.kind = 'payroll_register'""").fetchall():
        row = json.loads(r["data"])
        amounts = _register_amounts(row)
        conn.execute("INSERT INTO payroll_subledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (r["doc_id"], r["row_no"], r["month"], r["entity"], row["employee_id"], row["department"],
                      row["currency"], row["gross_pay"], fmt(amounts["income_tax"]), fmt(amounts["employee_social"]),
                      row["net_pay"], fmt(amounts["employer_social"]), fmt(amounts["employer_pension"])))
        payroll += 1
    rates, equity = stock_rates(conn), 0
    for r in conn.execute("""SELECT d.doc_id, r.row_no, d.month, r.data FROM documents d
            JOIN source_rows r USING (doc_id) WHERE d.kind = 'equity_report'""").fetchall():
        row = json.loads(r["data"])
        rate, basis = rates.get((r["month"], row["entity"]), (None, None))
        local = fmt(cents(Decimal(row["expense_month_usd"]) * Decimal(rate))) if rate else None
        conn.execute("INSERT INTO equity_subledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (r["doc_id"], r["row_no"], r["month"], row["entity"], row["grant_id"], row["employee_id"],
                      row["department"], row["status"], row["expense_month_usd"], rate, basis, local))
        equity += 1
    return {"payroll_rows": payroll, "equity_rows": equity}


@dataclass(frozen=True)
class Difference:
    period: str
    entity: str
    account: str
    department: str | None
    subledger: Decimal
    ledger: Decimal


def _ledger_totals(conn: sqlite3.Connection) -> dict[tuple, Decimal]:
    """(period, entity, account, department) -> posted net debit; liabilities only from payroll accruals."""
    totals: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    accruals = {r[0] for r in conn.execute("""SELECT DISTINCT entry_id FROM journal_lines WHERE account = '6000'""")}
    for r in conn.execute("""SELECT e.entry_id, e.period, e.entity, l.account, l.department, l.debit, l.credit
            FROM journal_lines l JOIN journal_entries e USING (entry_id) WHERE e.status = 'posted'"""):
        if r["account"].startswith("6"):
            totals[(r["period"], r["entity"], r["account"], r["department"])] += Decimal(r["debit"]) - Decimal(r["credit"])
        elif r["entry_id"] in accruals and r["account"] in ("2100", "2200", "2210", "2220"):
            totals[(r["period"], r["entity"], r["account"], None)] += Decimal(r["credit"]) - Decimal(r["debit"])
    return totals


def _subledger_totals(conn: sqlite3.Connection) -> dict[tuple, Decimal]:
    totals: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    for r in conn.execute("SELECT * FROM payroll_subledger"):
        key = (r["period"], r["entity"])
        totals[(*key, "6000", r["department"])] += Decimal(r["gross"])
        totals[(*key, "6100", r["department"])] += Decimal(r["employer_social"])
        totals[(*key, "6200", r["department"])] += Decimal(r["employer_pension"])
        totals[(*key, "2100", None)] += Decimal(r["net"])
        totals[(*key, "2200", None)] += Decimal(r["income_tax"])
        totals[(*key, "2210", None)] += Decimal(r["employee_social"]) + Decimal(r["employer_social"])
        totals[(*key, "2220", None)] += Decimal(r["employer_pension"])
    for r in conn.execute("SELECT * FROM equity_subledger WHERE expense_local IS NOT NULL"):
        totals[(r["period"], r["entity"], "6300", r["department"])] += Decimal(r["expense_local"])
    return totals


def booked_periods(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """(period, entity) pairs whose payroll has been posted to the ledger."""
    return {(r[0], r[1]) for r in conn.execute("""SELECT DISTINCT e.period, e.entity FROM journal_lines l
        JOIN journal_entries e USING (entry_id) WHERE l.account = '6000' AND e.status = 'posted'""")}


def rollup(conn: sqlite3.Connection) -> list[Difference]:
    """Every (period, entity, account, department) where a booked period's subledger and ledger disagree."""
    booked = booked_periods(conn)
    ledger, sub = _ledger_totals(conn), _subledger_totals(conn)
    out = []
    for key in sorted(set(ledger) | set(sub), key=lambda k: tuple(x or "" for x in k)):
        if (key[0], key[1]) not in booked:
            continue
        if ledger.get(key, ZERO) != sub.get(key, ZERO):
            out.append(Difference(*key, sub.get(key, ZERO), ledger.get(key, ZERO)))
    return out
