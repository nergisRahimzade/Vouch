"""B2: the chart of accounts is spec §4.3; history entries use only these accounts."""
from vouch.db.accounts import BY_CODE, CHART


def test_chart_is_the_spec_chart():
    assert {a.code: a.name for a in CHART} == {
        "1000": "Cash", "1310": "Prepaid event costs", "1400": "VAT receivable (ENT-UK)", "2000": "Accounts payable",
        "2100": "Accrued liabilities", "6100": "Event venue and space", "6110": "Event catering",
        "6120": "Event AV and production", "6130": "Event merchandise", "6140": "Event lodging",
        "6190": "Purchase discounts (contra-expense)"}
    assert all(a.needs_tags == (a.type == "expense") for a in CHART)


def test_accounts_table_matches_the_chart(db):
    rows = {r["code"]: (r["name"], r["type"], r["normal_side"]) for r in db.execute("SELECT * FROM accounts")}
    assert rows == {a.code: (a.name, a.type, a.normal_side) for a in CHART}


def test_every_history_line_uses_a_chart_account(db):
    accounts = {r[0] for r in db.execute("SELECT account FROM journal_lines")}
    assert accounts and accounts <= set(BY_CODE)
