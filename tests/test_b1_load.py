"""B1: every file stored as it arrived, with its hash; the rebuild reports what it loaded."""
import csv
import hashlib
import io
import json
import sqlite3

import pytest

from vouch.db.load import summary
from vouch.db.rebuild import rebuild, report
from vouch.world import config as C


def test_every_file_is_stored_byte_for_byte_with_its_hash(world, db):
    files = sorted(p for p in world.root.rglob("*") if p.is_file() and p.name != "manifest.json")
    stored = {r["path"]: r for r in db.execute("SELECT * FROM documents")}
    assert set(stored) == {p.relative_to(world.root).as_posix() for p in files}
    for file in files:
        row = stored[file.relative_to(world.root).as_posix()]
        content = file.read_bytes()
        assert bytes(row["content"]) == content
        assert row["sha256"] == hashlib.sha256(content).hexdigest() == row["manifest_sha256"]


def test_csv_rows_are_kept_in_order_and_unchanged(world, db):
    path = f"months/{C.OPEN_MONTH}/payroll_register_UK.csv"
    with (world.root / path).open(newline="", encoding="utf-8") as f:
        expected = list(csv.DictReader(f))
    rows = db.execute("SELECT data FROM source_rows JOIN documents USING (doc_id) WHERE path = ? ORDER BY row_no",
                      (path,)).fetchall()
    assert [json.loads(r["data"]) for r in rows] == expected


def test_loaded_documents_cannot_be_changed_or_deleted(db):
    with pytest.raises(sqlite3.DatabaseError, match="as they arrived"):
        db.execute("UPDATE source_rows SET data = '{}' WHERE row_no = 1")
    with pytest.raises(sqlite3.DatabaseError, match="as they arrived"):
        db.execute("DELETE FROM documents WHERE kind = 'hr_census'")


def test_rebuild_prints_people_months_and_periods(world, tmp_path):
    result = rebuild(world.root, tmp_path / "v.db")
    text = report(result)
    assert result["people_ever"] > 300 and result["people_active_open_month"] >= 290
    assert len(result["months"]) == 13 and result["open"] == [C.OPEN_MONTH] and len(result["locked"]) == 12
    for fragment in ("people:", "months:", "locked periods: 12", f"open period: {C.OPEN_MONTH}"):
        assert fragment in text


def test_the_answer_key_is_never_loaded(db):
    paths = [r[0] for r in db.execute("SELECT path FROM documents")]
    assert not any("sealed" in p or "answer_key" in p for p in paths)
    for (data,) in db.execute("SELECT data FROM source_rows"):
        assert "expected_outcome" not in data and "MAIN-" not in data
