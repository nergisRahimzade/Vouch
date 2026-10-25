"""Rebuild a database from the company world, from scratch: the base company, or one scenario."""
from __future__ import annotations

from pathlib import Path

from vouch.db import DEFAULT_DB, connect, create
from vouch.db.load import load, summary
from vouch.world.build import build
from vouch.world.scenario import Scenario


def rebuild(db: Path = DEFAULT_DB, scenario: Scenario | None = None, as_of: str | None = None) -> dict:
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    rows, meta = build(scenario, as_of)
    conn = connect(db)
    with conn:
        create(conn)
        loaded = load(conn, rows, meta)
    result = {**summary(conn), **loaded, "db": str(db)}
    conn.close()
    return result


def report(result: dict) -> str:
    rows = result["rows"]
    lines = [
        f"database rebuilt at {result['db']}: {result['scenario']} as of {result['as_of']}",
        f"rows: " + ", ".join(f"{n} {t}" for t, n in rows.items() if n),
        f"locked periods: {len(result['locked'])}" + (f" ({result['locked'][0]}..{result['locked'][-1]})"
                                                      if result["locked"] else ""),
        f"open periods: {', '.join(result['open'])}",
        f"general ledger: {result['entries_posted']} history entries posted, {len(result['entries_refused'])} refused",
    ]
    if result["entries_refused"]:
        lines.append(f"WARNING: history entries refused by the ledger: {', '.join(result['entries_refused'])}")
    return "\n".join(lines)
