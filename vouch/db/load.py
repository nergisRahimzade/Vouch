"""Loads a world directory into the database exactly as it arrived. After this step
everything reads the database; nothing reads the files or the generator."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MONTH_DIR = re.compile(r"^months/(\d{4}-\d{2})/")
ENTITY_SUFFIX = re.compile(r"_(US|UK|DE)$")
# JSON documents whose rows are one list inside them
JSON_ROWS = {"inbox": "emails", "rulings": "rulings", "pay_policy": "rules", "finance_policy": "rules",
             "directory": "people", "company": "entities"}


def describe(path: str) -> tuple[str, str | None, str | None]:
    """(kind, month, entity) from a path such as months/2026-03/payroll_register_US.csv."""
    stem = Path(path).stem
    month = MONTH_DIR.match(path)
    entity = ENTITY_SUFFIX.search(stem)
    kind = ENTITY_SUFFIX.sub("", stem)
    return kind, month.group(1) if month else None, entity.group(1) if entity else None


def parse_rows(path: str, kind: str, content: bytes) -> list[dict]:
    text = content.decode("utf-8")
    if path.endswith(".csv"):
        return list(csv.DictReader(io.StringIO(text)))
    document = json.loads(text)
    if kind in JSON_ROWS:
        return list(document[JSON_ROWS[kind]])
    return [document]


def load_world(conn: sqlite3.Connection, root: Path) -> dict:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["files"] if manifest_path.exists() else {}
    loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mismatches = []
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json")
    for file in files:
        path = file.relative_to(root).as_posix()
        content = file.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if manifest and manifest.get(path) != digest:
            mismatches.append(path)
        kind, month, entity = describe(path)
        rows = parse_rows(path, kind, content)
        cursor = conn.execute(
            "INSERT INTO documents (path, kind, month, entity, sha256, manifest_sha256, size_bytes, row_count, "
            "content, loaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (path, kind, month, entity, digest, manifest.get(path), len(content), len(rows), content, loaded_at))
        conn.executemany("INSERT INTO source_rows (doc_id, row_no, data) VALUES (?, ?, ?)",
                         [(cursor.lastrowid, n, json.dumps(row, ensure_ascii=False)) for n, row in enumerate(rows, 1)])
    months = [r[0] for r in conn.execute("SELECT DISTINCT month FROM documents WHERE month IS NOT NULL ORDER BY month")]
    # Every period starts open so history can be loaded; the rebuild then locks the closed ones.
    conn.executemany("INSERT INTO periods (period, status) VALUES (?, 'open')", [(m,) for m in months])
    return {"documents": len(files), "manifest_mismatches": mismatches}


def summary(conn: sqlite3.Connection) -> dict:
    open_month = conn.execute("SELECT period FROM periods WHERE status = 'open'").fetchone()
    people = conn.execute("SELECT COUNT(DISTINCT employee_id) FROM v_hr_census").fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(DISTINCT employee_id) FROM v_hr_census WHERE month = ? AND status = 'active'",
        (open_month[0] if open_month else None,)).fetchone()[0]
    return {
        "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "source_rows": conn.execute("SELECT COUNT(*) FROM source_rows").fetchone()[0],
        "people_ever": people, "people_active_open_month": active,
        "months": [r[0] for r in conn.execute("SELECT period FROM periods ORDER BY period")],
        "locked": [r[0] for r in conn.execute("SELECT period FROM periods WHERE status = 'locked' ORDER BY period")],
        "open": [r[0] for r in conn.execute("SELECT period FROM periods WHERE status = 'open' ORDER BY period")],
    }
