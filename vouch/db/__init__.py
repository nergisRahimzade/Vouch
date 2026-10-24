"""Layer 2: the system of record — every source file as loaded, and the general ledger."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "vouch.db"
SCHEMA_FILES = ["schema.sql", "ledger.sql", "periods.sql"]


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create(conn: sqlite3.Connection) -> None:
    from vouch.db import accounts, subledger

    for name in SCHEMA_FILES:
        conn.executescript((Path(__file__).with_name(name)).read_text(encoding="utf-8"))
    accounts.seed(conn)
    subledger.create_tables(conn)
