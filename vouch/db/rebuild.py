"""Rebuild the database from a world directory, from scratch."""
from __future__ import annotations

import json
from pathlib import Path

from vouch.db import DEFAULT_DB, connect, create, periods, subledger
from vouch.db.ledger import load_booked_ledger
from vouch.db.load import load_world, summary

DEFAULT_WORLD = Path(__file__).resolve().parents[2] / "world"


def rebuild(world: Path = DEFAULT_WORLD, db: Path = DEFAULT_DB) -> dict:
    if not (world / "manifest.json").exists():
        raise FileNotFoundError(f"no world at {world}; run `python -m vouch.world build` first")
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    conn = connect(db)
    with conn:
        create(conn)
        loaded = load_world(conn, world)
        ledger = load_booked_ledger(conn)
        lock_closed_months(conn)
        subledgers = subledger.build(conn)
    differences = subledger.rollup(conn)
    booked = subledger.booked_periods(conn)
    result = {**summary(conn), "manifest_mismatches": loaded["manifest_mismatches"], "db": str(db),
              "entries_posted": ledger["posted"], "entries_refused": ledger["refused"], **subledgers,
              "rollup_differences": len(differences), "periods_booked": len({p for p, _ in booked})}
    conn.close()
    return result


def lock_closed_months(conn) -> None:
    """Months before the open month were closed in earlier closes: the controller's sign-off locks them."""
    company = json.loads(conn.execute("SELECT content FROM documents WHERE kind = 'company'").fetchone()[0])
    controller = periods.directory_actor(conn, "controller")
    for (period,) in conn.execute("SELECT period FROM periods WHERE period < ? ORDER BY period",
                                  (company["open_month"],)).fetchall():
        periods.lock_period(conn, period, controller, "closed in an earlier close; loaded as history")


def report(result: dict) -> str:
    lines = [
        f"database rebuilt at {result['db']}: {result['documents']} documents, {result['source_rows']} source rows",
        f"people: {result['people_ever']} ever employed, {result['people_active_open_month']} active in the open month",
        f"months: {result['months'][0]}..{result['months'][-1]} ({len(result['months'])})",
        f"locked periods: {len(result['locked'])} ({result['locked'][0]}..{result['locked'][-1]})" if result["locked"]
        else "locked periods: none",
        f"open period: {', '.join(result['open']) or 'none'}",
        f"general ledger: {result['entries_posted']} booked entries posted, {len(result['entries_refused'])} refused",
        f"subledgers: {result['payroll_rows']} payroll rows, {result['equity_rows']} equity rows; "
        f"roll-up to the ledger: {result['rollup_differences']} differences over {result['periods_booked']} booked periods",
    ]
    if result["manifest_mismatches"]:
        lines.append(f"WARNING: {len(result['manifest_mismatches'])} files differ from the manifest")
    return "\n".join(lines)
