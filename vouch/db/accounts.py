"""The chart of accounts (spec §4.3). Every journal line must use one of these accounts."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    type: str          # asset | liability | expense
    normal_side: str   # debit | credit
    needs_tags: bool   # expense lines carry cost center and event tags


CHART = [
    Account("1000", "Cash", "asset", "debit", False),
    Account("1310", "Prepaid event costs", "asset", "debit", False),
    Account("1400", "VAT receivable (ENT-UK)", "asset", "debit", False),
    Account("2000", "Accounts payable", "liability", "credit", False),
    Account("2100", "Accrued liabilities", "liability", "credit", False),
    Account("6100", "Event venue and space", "expense", "debit", True),
    Account("6110", "Event catering", "expense", "debit", True),
    Account("6120", "Event AV and production", "expense", "debit", True),
    Account("6130", "Event merchandise", "expense", "debit", True),
    Account("6140", "Event lodging", "expense", "debit", True),
    Account("6190", "Purchase discounts (contra-expense)", "expense", "credit", True),
]
BY_CODE = {a.code: a for a in CHART}
EXPENSE = {a.code for a in CHART if a.type == "expense"}

# Which expense account a vendor category's goods and services go to.
CATEGORY_ACCOUNT = {"venue": "6100", "catering": "6110", "av": "6120", "merchandise": "6130", "lodging": "6140"}

SCHEMA = """
CREATE TABLE accounts (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('asset', 'liability', 'expense')),
    normal_side TEXT NOT NULL CHECK (normal_side IN ('debit', 'credit')),
    needs_tags  INTEGER NOT NULL
);
"""


def seed(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                     [(a.code, a.name, a.type, a.normal_side, int(a.needs_tags)) for a in CHART])
