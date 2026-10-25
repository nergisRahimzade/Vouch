"""B2: the chart of accounts is spec §4.3; history entries use only these accounts."""
from vouch.db.accounts import BY_CODE, CHART


def test_chart_is_the_spec_chart():
    assert [a.code for a in CHART] == ["1200", "1400", "1600", "1750", "1760", "6400", "6410", "6420", "6430", "6440",
                                       "6445", "6450", "6460", "6470", "6480", "6490", "6500"]
    assert BY_CODE["6430"].name == "Gifts — non-deductible (incl. blocked VAT)"
    assert all(a.needs_tags == (a.type == "expense") for a in CHART)


def test_accounts_table_matches_the_chart(db):
    rows = {r["code"]: (r["name"], r["type"], r["normal_side"]) for r in db.execute("SELECT * FROM accounts")}
    assert rows == {a.code: (a.name, a.type, a.normal_side) for a in CHART}


def test_every_history_line_uses_a_chart_account(db):
    accounts = {r[0] for r in db.execute("SELECT account FROM journal_lines")}
    assert accounts and accounts <= set(BY_CODE)
