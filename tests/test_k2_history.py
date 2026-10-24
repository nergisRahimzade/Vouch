"""K2: checks scan every loaded month; mistakes in closed months are found in the open month's
close; past rulings are stored and reused."""
import json
import shutil

from vouch.db import connect
from vouch.evaluator.match import match
from vouch.recon.run import reconcile, stored


def test_mistakes_in_closed_months_are_found_in_the_open_close(recon):
    findings, key = recon
    open_month = key["open_month"]
    closed = [p for p in key["plants"] if p["month"] < open_month]
    assert len(closed) >= 5
    found = match(closed, findings)
    for plant in closed:
        hits = found[plant["plant_id"]]
        assert hits, plant["plant_id"]
        assert all(h["period"] < open_month for h in hits), plant["plant_id"]


def test_findings_are_stored_under_the_open_months_close(db_path, tmp_path, recon):
    _, key = recon
    copy = tmp_path / "v.db"
    shutil.copyfile(db_path, copy)
    conn = connect(copy)
    reconcile(conn)
    closes = {r[0] for r in conn.execute("SELECT DISTINCT close_period FROM findings")}
    periods = {r[0] for r in conn.execute("SELECT DISTINCT period FROM findings")}
    assert closes == {key["open_month"]} and len(periods) > 3


def test_past_rulings_are_stored_and_cited(recon, db_path, tmp_path):
    findings, key = recon
    by_plant = match(key["plants"], findings)
    for plant in key["plants"]:
        cited = {p["ruling_id"] for f in by_plant[plant["plant_id"]] for p in f["precedents"]}
        assert set(plant.get("precedents", [])) <= cited, plant["plant_id"]
    copy = tmp_path / "v.db"
    shutil.copyfile(db_path, copy)
    conn = connect(copy)
    reconcile(conn)
    assert conn.execute("SELECT COUNT(*) FROM rulings").fetchone()[0] == 3


def test_a_new_ruling_is_reused_by_the_next_close(db_path, tmp_path):
    copy = tmp_path / "v.db"
    shutil.copyfile(db_path, copy)
    conn = connect(copy)
    reconcile(conn)
    kind = next(r["kind"] for r in stored(conn) if r["disposition"] == "case")
    conn.execute("INSERT INTO rulings VALUES ('RULING-TEST', '2000-01', '2000-01-31', ?, NULL, 'rejected', "
                 "'Controller', '[]', 'Do not resend unchanged.', 'test')", (kind,))
    conn.commit()
    reconcile(conn)
    cited = [f for f in stored(conn) if f["kind"] == kind]
    assert cited and all(any(p["ruling_id"] == "RULING-TEST" for p in f["precedents"]) for f in cited)
    assert reconcile(conn) and conn.execute("SELECT COUNT(*) FROM rulings WHERE ruling_id = 'RULING-TEST'").fetchone()[0] == 1


def test_running_the_close_twice_creates_no_duplicates(db_path, tmp_path):
    copy = tmp_path / "v.db"
    shutil.copyfile(db_path, copy)
    conn = connect(copy)
    reconcile(conn)
    first = sorted(json.dumps(r, sort_keys=True) for r in stored(conn))
    reconcile(conn)
    assert sorted(json.dumps(r, sort_keys=True) for r in stored(conn)) == first
