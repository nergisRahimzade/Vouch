"""T2: the base fixtures and generated people load exactly and reproducibly, and the time-travel loader
hides every row dated after as_of (D19)."""
import json
import sqlite3

import pytest

from vouch.db.rebuild import rebuild
from vouch.world.generator import expand
from vouch.world.scenario import ScenarioError, parse, rows_for


def _dump(path):
    conn = sqlite3.connect(path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    out = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in tables}
    conn.close()
    out["journal_entries"] = [row[:-1] for row in out["journal_entries"]]        # created_at is the load time
    out.pop("period_events")
    return out


def test_two_builds_are_identical(tmp_path):
    rebuild(tmp_path / "a.db")
    rebuild(tmp_path / "b.db")
    assert _dump(tmp_path / "a.db") == _dump(tmp_path / "b.db")


def test_ranges_expand():
    assert expand("E001-E003") == ["E001", "E002", "E003"]
    assert expand("BP-099-BP-101") == ["BP-099", "BP-100", "BP-101"]
    assert expand(["E010", "E020"]) == ["E010", "E020"] and expand(None) == []


def test_appendix_a_is_seeded_exactly(db):
    count = lambda t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    assert (count("entities"), count("cost_centers"), count("employees"), count("people"), count("business_partners"),
            count("vendors"), count("events"), count("elections")) == (1, 4, 450, 7, 600, 8, 3, 2)
    assert tuple(db.execute("SELECT state, currency FROM entities").fetchone()) == ("Bavaria", "EUR")
    units = dict(db.execute("SELECT cost_center, COUNT(*) FROM employees GROUP BY cost_center").fetchall())
    assert units == {"CC-PROD": 200, "CC-SALES": 100, "CC-ENG": 100, "CC-ADMIN": 50}
    managers = [r[0] for r in db.execute("SELECT id FROM employees WHERE is_manager = 1 ORDER BY id")]
    assert len(managers) == 25 and managers[0] == "E010" and managers[-1] == "E440"
    assert tuple(db.execute("SELECT name, company FROM business_partners WHERE id = 'BP-003'").fetchone()) == \
        ("Dr. Klaus Meier", "Bergmann Automotive AG")
    assert db.execute("SELECT elected FROM elections WHERE scope = 'third_parties'").fetchone()[0] == 1


def test_employee_names_are_deterministic(db):
    # Faker de_DE seed 42, pinned version: the first three names never move.
    assert [r[0] for r in db.execute("SELECT name FROM employees ORDER BY id LIMIT 3")] == \
        ["Aleksandr Weihmann", "Eleni Hauffer", "Paul Dobes-Stey"]


def test_rules_are_seeded_with_sources_and_versions(db):
    rules = {(r["rule_id"], r["version"]): dict(r) for r in db.execute("SELECT * FROM rules")}
    assert len({r for r, _ in rules}) == 23
    assert rules[("R-BV-ALLOW", 1)]["value"] == "110.00" and rules[("TAX-KIST", 1)]["value"] == "0.07"
    assert (rules[("R-GIFT-VER", 1)]["value"], rules[("R-GIFT-VER", 1)]["valid_to"]) == ("35.00", "2023-12-31")
    assert (rules[("R-GIFT-VER", 2)]["value"], rules[("R-GIFT-VER", 2)]["valid_from"]) == ("50.00", "2024-01-01")
    assert all(r["source_url"] and r["basis"] for r in rules.values())
    policies = {r[0] for r in db.execute("SELECT id FROM policies")}
    assert policies == {"POL-BV-OPT", "POL-BV-VATBASIS", "POL-BV-FLAT", "POL-BEW-02", "POL-LUMP", "POL-37B-01",
                        "POL-ROUND", "POL-MAT"}


def test_attendance_expands_with_companions(db):
    kinds = dict(db.execute("SELECT person_kind, COUNT(*) FROM attendance WHERE event = 'EVT-SOM26' "
                            "GROUP BY person_kind").fetchall())
    assert kinds == {"employee": 300, "companion": 60}
    hosts = db.execute("SELECT MIN(host_employee), MAX(host_employee) FROM attendance WHERE person_kind = 'companion'")
    assert tuple(hosts.fetchone()) == ("E201", "E260")


def test_history_is_booked(db):
    lines = db.execute("SELECT account, debit, credit FROM journal_lines WHERE entry_id = 'JE-INV-FAS-01' "
                       "ORDER BY line_no").fetchall()
    assert [tuple(r) for r in lines] == [("6440", "6000.00", "0.00"), ("1400", "1140.00", "0.00"),
                                         ("1600", "0.00", "7140.00")]
    gift = db.execute("SELECT account, debit FROM journal_lines WHERE entry_id = 'JE-GIFT-0318-37B' AND debit <> '0.00'")
    assert tuple(gift.fetchone()) == ("6450", "12.05")
    assert db.execute("SELECT recipient FROM gift_distributions").fetchone()[0] == "BP-003"
    assert db.execute("SELECT COUNT(*) FROM refused_entries").fetchone()[0] == 0


def test_rows_after_as_of_are_hidden(scenario_db):
    july = scenario_db({"id": "T", "as_of": "2026-07-07"})
    invoices = {r[0] for r in july.execute("SELECT id FROM invoices")}
    assert {"INV-SOM-VEN", "INV-SOM-CAT"} <= invoices and "INV-SOM-BUS" not in invoices
    june = scenario_db({"id": "T", "as_of": "2026-06-30"})
    assert june.execute("SELECT COUNT(*) FROM attendance WHERE event = 'EVT-SOM26'").fetchone()[0] == 0
    early = scenario_db({"id": "T", "as_of": "2026-01-10"})
    assert early.execute("SELECT COUNT(*) FROM elections").fetchone()[0] == 0          # decided 2026-01-15
    assert early.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 0


def test_a_month_closes_after_the_10th_of_the_next(scenario_db):
    on_tenth = scenario_db({"id": "T", "as_of": "2026-08-10"})
    after = scenario_db({"id": "T", "as_of": "2026-08-11"})
    status = lambda c, p: c.execute("SELECT status FROM periods WHERE period = ?", (p,)).fetchone()[0]
    assert status(on_tenth, "2026-07") == "open" and status(after, "2026-07") == "locked"
    assert status(after, "2026-08") == "open"


def test_setup_compact_forms_and_overrides(scenario_db):
    conn = scenario_db({
        "id": "T", "as_of": "2026-12-18",
        "setup": {"attendance": [{"event": "EVT-XMAS26", "employees": "E001-E150", "business_partners": "BP-001-BP-002",
                                  "companions": [{"host": "E001", "count": 2}]}],
                  "payroll_exports": [{"event": "EVT-SOM26", "employees": "E201-E260", "taxable_each": "104.86",
                                       "period": "2026-07", "method": "flat_40"}],
                  "invoices": [{"id": "INV-X", "vendor": "V-CATER", "received_at": "2026-12-12", "portal_ref": "ABC",
                                "lines": [{"category": "catering", "net": "100.00", "vat": "19.00", "gross": "119.00"}]}]},
        "overrides": {"events": {"EVT-XMAS26": {"open_to": "CC-ENG"}}}})
    kinds = dict(conn.execute("SELECT person_kind, COUNT(*) FROM attendance WHERE event = 'EVT-XMAS26' "
                              "GROUP BY person_kind").fetchall())
    assert kinds == {"employee": 150, "business_partner": 2, "companion": 2}
    exports = conn.execute("SELECT COUNT(*), MIN(taxable_amount) FROM payroll_exports").fetchone()
    assert tuple(exports) == (60, "104.86")
    assert conn.execute("SELECT open_to FROM events WHERE id = 'EVT-XMAS26'").fetchone()[0] == "CC-ENG"
    invoice = dict(conn.execute("SELECT * FROM invoices WHERE id = 'INV-X'").fetchone())
    assert (invoice["gross"], json.loads(invoice["extra"])) == ("119.00", {"portal_ref": "ABC"})


def test_an_override_of_a_row_that_does_not_exist_is_refused():
    with pytest.raises(ScenarioError):
        rows_for(parse({"id": "T", "as_of": "2026-06-20", "overrides": {"events": {"EVT-NOPE": {"date": "x"}}}}))


def test_the_expect_block_never_reaches_a_scenario():
    scenario = parse({"id": "T", "as_of": "2026-06-20", "expect": {"decision": "classify_and_book"}})
    assert "expect" not in vars(scenario) and "classify_and_book" not in repr(scenario)


def test_elections_and_rules_are_read_only(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE elections SET elected = 0")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO elections (id, year, scope, regime, elected) VALUES ('X', 2027, 'x', '37b', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE rules SET value = '120.00' WHERE rule_id = 'R-BV-ALLOW'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO payroll_exports (id, period, employee, taxable_amount, method, status) "
                   "VALUES ('X', '2026-12', 'E001', '1.00', 'flat_40', 'accepted')")
