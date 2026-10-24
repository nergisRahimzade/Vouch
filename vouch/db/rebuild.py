"""Rebuild the database from a world directory, from scratch."""
from __future__ import annotations

from pathlib import Path

from vouch.db import DEFAULT_DB, connect, create
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
    result = {**summary(conn), "manifest_mismatches": loaded["manifest_mismatches"], "db": str(db)}
    conn.close()
    return result


def report(result: dict) -> str:
    lines = [
        f"database rebuilt at {result['db']}: {result['documents']} documents, {result['source_rows']} source rows",
        f"people: {result['people_ever']} ever employed, {result['people_active_open_month']} active in the open month",
        f"months: {result['months'][0]}..{result['months'][-1]} ({len(result['months'])})",
        f"locked periods: {len(result['locked'])} ({result['locked'][0]}..{result['locked'][-1]})" if result["locked"]
        else "locked periods: none",
        f"open period: {', '.join(result['open']) or 'none'}",
    ]
    if result["manifest_mismatches"]:
        lines.append(f"WARNING: {len(result['manifest_mismatches'])} files differ from the manifest")
    return "\n".join(lines)
