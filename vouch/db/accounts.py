"""The chart of accounts (spec §4.3, simulated SKR-style names). Every journal line must use one of these."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    type: str          # asset | liability | expense
    normal_side: str   # debit | credit
    needs_tags: bool   # expense lines carry event and cost center tags


CHART = [
    Account("1200", "Bank", "asset", "debit", False),
    Account("1400", "Input VAT receivable (Vorsteuer)", "asset", "debit", False),
    Account("1600", "Accounts payable", "liability", "credit", False),
    Account("1750", "Payroll flat tax payable (§40)", "liability", "credit", False),
    Account("1760", "Guest flat tax payable (§37b)", "liability", "credit", False),
    Account("6400", "Hospitality — deductible 70%", "expense", "debit", True),
    Account("6410", "Hospitality — non-deductible 30%", "expense", "debit", True),
    Account("6420", "Gifts — deductible", "expense", "debit", True),
    Account("6430", "Gifts — non-deductible (incl. blocked VAT)", "expense", "debit", True),
    Account("6440", "Staff events", "expense", "debit", True),
    Account("6445", "Customer events / program (non-gift benefits)", "expense", "debit", True),
    Account("6450", "§37b flat tax — deductible", "expense", "debit", True),
    Account("6460", "§37b flat tax — non-deductible", "expense", "debit", True),
    Account("6470", "§40 flat tax on staff events", "expense", "debit", True),
    Account("6480", "Promo items (Streuwerbeartikel)", "expense", "debit", True),
    Account("6490", "Working meals", "expense", "debit", True),
    Account("6500", "Wages — non-cash benefits (individually taxed)", "expense", "debit", True),
]
BY_CODE = {a.code: a for a in CHART}
EXPENSE = {a.code for a in CHART if a.type == "expense"}
NONDEDUCTIBLE = {"6410", "6430", "6460"}       # expense accounts that are not deductible for income tax

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
