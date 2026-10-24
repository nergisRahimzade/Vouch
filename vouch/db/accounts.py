"""The chart of accounts. Every journal line must use one of these accounts."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    type: str          # asset | liability | equity | expense | suspense
    normal_side: str   # debit | credit
    needs_department: bool


CHART = [
    Account("1000", "Cash at bank", "asset", "debit", False),
    Account("2100", "Net pay payable", "liability", "credit", False),
    Account("2200", "Income tax withholding payable", "liability", "credit", False),
    Account("2210", "Social contributions payable", "liability", "credit", False),
    Account("2220", "Pension contributions payable", "liability", "credit", False),
    Account("3100", "Additional paid-in capital", "equity", "credit", False),
    Account("6000", "Wages and salaries", "expense", "debit", True),
    Account("6100", "Employer payroll taxes", "expense", "debit", True),
    Account("6200", "Employer pension", "expense", "debit", True),
    Account("6300", "Stock-based compensation", "expense", "debit", True),
    Account("9999", "Suspense", "suspense", "debit", False),
]
BY_CODE = {a.code: a for a in CHART}

SCHEMA = """
CREATE TABLE accounts (
    code              TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN ('asset', 'liability', 'equity', 'expense', 'suspense')),
    normal_side       TEXT NOT NULL CHECK (normal_side IN ('debit', 'credit')),
    needs_department  INTEGER NOT NULL
);
"""


def seed(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                     [(a.code, a.name, a.type, a.normal_side, int(a.needs_department)) for a in CHART])
