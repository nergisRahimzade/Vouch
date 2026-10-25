"""T2: the base fixtures load exactly and reproducibly, and the time-travel loader hides every row
dated after as_of (D19)."""
import json
import sqlite3

import pytest

from vouch.db.rebuild import rebuild
from vouch.world.scenario import ScenarioError, parse, rows_for


def _dump(path):
    conn = sqlite3.connect(path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    out = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in tables}
    conn.close()
    for t in ("journal_entries",):                      # created_at is the load time
        out[t] = [row[:-1] for row in out[t]]
    out.pop("period_events")
    return out


def test_two_builds_are_identical(tmp_path):
    rebuild(tmp_path / "a.db")
    rebuild(tmp_path / "b.db")
    assert _dump(tmp_path / "a.db") == _dump(tmp_path / "b.db")


def test_appendix_a_is_seeded_exactly(db):
    count = lambda t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    assert (count("entities"), count("people"), count("events"), count("vendors"), count("contracts"),
            count("contract_clauses"), count("purchase_orders")) == (2, 8, 5, 8, 9, 32, 12)
    gfork = dict(db.execute("SELECT * FROM vendors WHERE id = 'V-GFORK'").fetchone())
    assert gfork["enhanced_review_until"] == "2026-12-31" and gfork["phone_on_file"] == "+1-415-555-0188"
    clause = dict(db.execute("SELECT * FROM contract_clauses WHERE clause_id = 'CAT-GUARANTEE'").fetchone())
    assert json.loads(clause["params"])["guaranteed_count"] == 300
    assert db.execute("SELECT value FROM policies WHERE id = 'TAX-SF'").fetchone()[0] == "0.08625"
    rates = db.execute("SELECT period, rate FROM period_rates WHERE pair = 'GBP/USD' ORDER BY period").fetchall()
    assert [r[0] for r in rates] == [f"2026-{m:02d}" for m in range(1, 13)] and {r[1] for r in rates} == {"1.27"}
    assert db.execute("SELECT account_last4 FROM vendor_bank_accounts WHERE vendor = 'V-BAYV'").fetchone()[0] == "4417"


def test_amounts_are_never_floats():
    rows, _ = rows_for()
    params = rows["contract_clauses"][0]["params"]
    assert str(params["amount"]) == "120000.00" and not isinstance(params["amount"], float)


def test_deposits_become_invoice_payment_and_prepaid_entries(db):
    lines = db.execute("SELECT account, debit, credit FROM journal_lines WHERE entry_id = 'JE-TH-4300' "
                       "ORDER BY line_no").fetchall()
    assert [tuple(r) for r in lines] == [("1310", "11400.00", "0.00"), ("1400", "2280.00", "0.00"),
                                         ("2000", "0.00", "13680.00")]
    payment = dict(db.execute("SELECT * FROM payments WHERE invoice = 'GF-1100'").fetchone())
    assert (payment["amount"], payment["paid_at"], payment["account_last4"]) == ("6375.00", "2026-04-15", "8820")
    invoice = dict(db.execute("SELECT * FROM invoices WHERE id = 'INV-BV-5400'").fetchone())
    assert invoice["received_at"] == "2026-02-25"                 # paid_at minus 5 days
    assert db.execute("SELECT COUNT(*) FROM refused_entries").fetchone()[0] == 0


def test_rows_after_as_of_are_hidden(scenario_db):
    before = scenario_db({"id": "T", "as_of": "2026-09-09"})
    assert not before.execute("SELECT 1 FROM invoices WHERE id = 'INV-HP-0412'").fetchone()
    on_receipt = scenario_db({"id": "T", "as_of": "2026-09-12"})
    assert on_receipt.execute("SELECT 1 FROM invoices WHERE id = 'INV-HP-0412'").fetchone()
    assert not on_receipt.execute("SELECT 1 FROM payments WHERE invoice = 'INV-HP-0412'").fetchone()
    assert not on_receipt.execute("SELECT 1 FROM journal_entries WHERE source_ref = 'INV-HP-0412'").fetchone()
    early = scenario_db({"id": "T", "as_of": "2026-06-10"})
    types = {r[0] for r in early.execute("SELECT id FROM evidence")}
    assert "EV-SF26-BADGE-0611" not in types and "EV-SF26-DELIV-GIFT" in types


def test_a_month_closes_after_the_5th_of_the_next(scenario_db):
    on_fifth = scenario_db({"id": "T", "as_of": "2026-10-05"})
    after = scenario_db({"id": "T", "as_of": "2026-10-06"})
    status = lambda c, p: c.execute("SELECT status FROM periods WHERE period = ?", (p,)).fetchone()[0]
    assert status(on_fifth, "2026-09") == "open" and status(after, "2026-09") == "locked"
    assert status(after, "2026-10") == "open" and status(after, "2026-12") == "open"


def test_setup_and_overrides(scenario_db):
    conn = scenario_db({
        "id": "T", "as_of": "2026-06-20",
        "setup": {"invoices": [{"id": "INV-X", "vendor": "V-SWAG", "received_at": "2026-06-18", "total": "10.00",
                                "portal_ref": "ABC", "lines": [{"kind": "item", "amount": "10.00"}]}]},
        "overrides": {"evidence": {"EV-SF26-DELIV-HOOD": {"value": {"qty": 310}}},
                      "contract_clauses": {"CAT-STAFF": {"params": {"counts_toward_headcount": True}}},
                      "vendor_bank_accounts": {"V-SWAG:5562": {"active_to": "2026-12-31"}}}})
    value = json.loads(conn.execute("SELECT value FROM evidence WHERE id = 'EV-SF26-DELIV-HOOD'").fetchone()[0])
    assert value == {"vendor": "V-SWAG", "item": "hoodie", "qty": 310}          # merged, not replaced
    params = json.loads(conn.execute("SELECT params FROM contract_clauses WHERE clause_id = 'CAT-STAFF'").fetchone()[0])
    assert params == {"counts_toward_headcount": True}
    assert conn.execute("SELECT active_to FROM vendor_bank_accounts WHERE vendor = 'V-SWAG'").fetchone()[0] == "2026-12-31"
    invoice = dict(conn.execute("SELECT * FROM invoices WHERE id = 'INV-X'").fetchone())
    assert json.loads(invoice["extra"]) == {"portal_ref": "ABC"}               # unknown fields kept, not dropped
    assert conn.execute("SELECT amount FROM invoice_lines WHERE invoice = 'INV-X'").fetchone()[0] == "10.00"


def test_an_override_of_a_row_that_does_not_exist_is_refused():
    with pytest.raises(ScenarioError):
        rows_for(parse({"id": "T", "as_of": "2026-06-20", "overrides": {"evidence": {"EV-NOPE": {"date": "x"}}}}))


def test_a_call_back_after_as_of_is_not_visible(scenario_db):
    change = {"id": "VMC-1", "vendor": "V-BAYV", "field": "bank_account", "new_value": "7781",
              "requested_at": "2026-07-01", "callback_by": "P-VMA", "callback_at": "2026-07-15",
              "changed_by": "P-VMA", "approved_by": "P-CTRL"}
    conn = scenario_db({"id": "T", "as_of": "2026-07-10", "setup": {"vendor_master_changes": [change]}})
    row = dict(conn.execute("SELECT * FROM vendor_master_changes").fetchone())
    assert row["callback_at"] is None and row["approved_by"] is None


def test_the_expect_block_never_reaches_a_scenario():
    scenario = parse({"id": "T", "as_of": "2026-06-20", "expect": {"decision": "pay_full"}})
    assert "expect" not in vars(scenario) and "pay_full" not in repr(scenario)


def test_clauses_without_a_type_are_typed_from_their_terms(scenario_db):
    conn = scenario_db({"id": "T", "as_of": "2026-06-20", "setup": {"contract_clauses": [
        {"clause_id": "X-1", "contract": "C-LUMEN-SF26", "params": {"tiers": [{"min_days": 60, "pct": 0.5}]}},
        {"clause_id": "X-2", "contract": "C-LUMEN-SF26", "params": {"price_per_head": 10}},
        {"clause_id": "X-3", "contract": "C-LUMEN-SF26", "params": {"note": "unclear"}}]}})
    types = dict(conn.execute("SELECT clause_id, type FROM contract_clauses WHERE clause_id LIKE 'X-%'").fetchall())
    assert types == {"X-1": "cancellation", "X-2": "per_head_price", "X-3": None}


def test_vendor_master_data_is_read_only(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE vendor_bank_accounts SET account_last4 = '0042' WHERE vendor = 'V-SWAG'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO payments (id, invoice, amount) VALUES ('P-X', 'GF-1100', '1.00')")
