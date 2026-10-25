"""The audit trail: append-only and hash-chained. Each row records who did what, when, why,
and on what basis (data, rule, document or person). Editing any past row breaks the chain."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from vouch.controls import clock

GENESIS = "0" * 64


def _canonical(at, actor, role, action, target, outcome, reason, basis, recorded_at=None) -> str:
    fields = [at, actor, role, action, target, outcome, reason, basis]
    if recorded_at is not None:       # rows written before the column existed hash as they were written
        fields.append(recorded_at)
    return json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)


def _hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def log(conn: sqlite3.Connection, actor, action: str, target: str, outcome: str, reason: str,
        basis: dict | list | None = None) -> int:
    """Append one row. `actor` has .name and .role."""
    at = clock.now(conn)
    recorded = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    basis_text = json.dumps(basis or {}, sort_keys=True, ensure_ascii=False, default=str)
    last = conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev = last[0] if last else GENESIS
    digest = _hash(prev, _canonical(at, actor.name, actor.role, action, target, outcome, reason, basis_text, recorded))
    cursor = conn.execute(
        "INSERT INTO audit_log (at, actor, role, action, target, outcome, reason, basis, prev_hash, hash, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (at, actor.name, actor.role, action, target, outcome, reason, basis_text, prev, digest, recorded))
    return cursor.lastrowid


@dataclass(frozen=True)
class Verification:
    ok: bool
    rows: int
    first_bad_seq: int | None = None
    problem: str = ""


def verify(conn: sqlite3.Connection) -> Verification:
    prev, count = GENESIS, 0
    for r in conn.execute("SELECT * FROM audit_log ORDER BY seq"):
        count += 1
        if r["prev_hash"] != prev:
            return Verification(False, count, r["seq"], "row does not follow the one before it")
        expected = _hash(prev, _canonical(r["at"], r["actor"], r["role"], r["action"], r["target"], r["outcome"],
                                          r["reason"], r["basis"], r["recorded_at"] if "recorded_at" in r.keys() else None))
        if expected != r["hash"]:
            return Verification(False, count, r["seq"], "row content does not match its hash")
        prev = r["hash"]
    return Verification(True, count)


def trail(conn: sqlite3.Connection, target_like: str | None = None) -> list[dict]:
    query = "SELECT * FROM audit_log" + (" WHERE target LIKE ? OR basis LIKE ?" if target_like else "") + " ORDER BY seq"
    params = (f"%{target_like}%", f"%{target_like}%") if target_like else ()
    return [{**dict(r), "basis": json.loads(r["basis"])} for r in conn.execute(query, params)]
