"""W4: each kind of noise appears in the data, and nothing else changes."""
from collections import Counter
from decimal import Decimal
from functools import lru_cache

from vouch import dates
from vouch.world import config as C
from vouch.world.build import generate
from vouch.world.policies import FINANCE_POLICY
from worlddiff import diff

OPEN = C.OPEN_MONTH


@lru_cache(maxsize=1)
def noisy():
    quiet = generate(clean=True, noise=False).bundle.files
    result = generate(clean=True)
    return {n.plant_id: n.key for n in result.noise}, result.bundle.files, diff(quiet, result.bundle.files), quiet


def test_email_typos_appear_in_routine_emails():
    keys, files, _, quiet = noisy()
    ids = keys["NOISE-01"]["subject"]["email_ids"]
    before = {e["email_id"]: e["body"] for e in quiet["standing/inbox.json"]["emails"]}
    after = {e["email_id"]: e for e in files["standing/inbox.json"]["emails"]}
    assert len(ids) == 6
    for email_id in ids:
        assert after[email_id]["body"] != before[email_id] and after[email_id]["from"].endswith(C.DOMAIN)
    assert sum(before[i] != after[i]["body"] for i in before) == 6


def test_duplicate_hr_row_appears():
    keys, files, _, _ = noisy()
    census = files[f"months/{OPEN}/hr_census.csv"]
    counts = Counter(r["employee_id"] for r in census)
    assert [e for e, n in counts.items() if n > 1] == [keys["NOISE-02"]["subject"]["employee_id"]]
    rows = [r for r in census if r["employee_id"] == keys["NOISE-02"]["subject"]["employee_id"]]
    assert rows[0] == rows[1]


def test_bank_line_posted_the_day_after_month_end():
    keys, files, _, _ = noisy()
    subject = keys["NOISE-03"]["subject"]
    run, following = keys["NOISE-03"]["month"], dates.add_months(keys["NOISE-03"]["month"], 1)
    assert subject["payment_id"] not in {b["reference"] for b in files[f"months/{run}/bank_statement_DE.csv"]}
    line = next(b for b in files[f"months/{following}/bank_statement_DE.csv"] if b["reference"] == subject["payment_id"])
    assert line["date"] == subject["posted"] and dates.month_of(dates.parse_date(line["date"])) == following
    booked = [l for l in files[f"months/{following}/ledger_booked.csv"] if l["source_ref"] == line["bank_line_id"]]
    assert len(booked) == 1 and booked[0]["account"] == "1000"


def test_fx_difference_is_inside_materiality():
    keys, files, d, _ = noisy()
    threshold = Decimal(FINANCE_POLICY["rules"][0]["threshold"]["EUR"])
    difference = Decimal(keys["NOISE-04"]["amount"]["value"])
    assert difference != 0 and abs(difference) < threshold
    change = d[f"months/{OPEN}/equity_report.csv"]["changed"]
    assert change and all(set(f) <= {"fx_rate", "expense_month_local"} for f in change.values())


def test_noise_changes_nothing_else():
    keys, _, d, _ = noisy()
    run, following = keys["NOISE-03"]["month"], dates.add_months(keys["NOISE-03"]["month"], 1)
    assert set(d) == {"standing/inbox.json", f"months/{OPEN}/hr_census.csv", f"months/{OPEN}/equity_report.csv",
                      f"months/{run}/bank_statement_DE.csv", f"months/{run}/ledger_booked.csv",
                      f"months/{following}/bank_statement_DE.csv", f"months/{following}/ledger_booked.csv"}
