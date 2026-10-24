"""B2: the chart of accounts covers what payroll and stock compensation need; the booked
ledger uses only these accounts."""
import json

from vouch.db.accounts import BY_CODE, CHART


def test_chart_covers_every_required_kind_of_account():
    names = " ".join(a.name.lower() for a in CHART)
    for needed in ("wages", "employer payroll taxes", "pension", "stock-based compensation", "net pay payable",
                   "income tax withholding payable", "social contributions payable", "cash", "paid-in capital",
                   "suspense"):
        assert needed in names, needed
    assert {a.type for a in CHART} == {"asset", "liability", "equity", "expense", "suspense"}
    assert all(a.needs_department == (a.type == "expense") for a in CHART)


def test_accounts_table_matches_the_chart(db):
    rows = {r["code"]: (r["name"], r["type"], r["normal_side"]) for r in db.execute("SELECT * FROM accounts")}
    assert rows == {a.code: (a.name, a.type, a.normal_side) for a in CHART}


def test_every_booked_ledger_line_uses_a_chart_account(db):
    lines = db.execute("SELECT data FROM source_rows JOIN documents USING (doc_id) WHERE kind = 'ledger_booked'")
    seen = set()
    for (data,) in lines:
        line = json.loads(data)
        assert line["account"] in BY_CODE, line
        assert line["account_name"] == BY_CODE[line["account"]].name, line
        seen.add(line["account"])
    assert seen >= {"1000", "2100", "2200", "2210", "2220", "3100", "6000", "6100", "6200", "6300"}
