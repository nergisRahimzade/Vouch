"""All plants and all noise in one build: no two touch the same subject, and every one
still holds in the combined files."""
from collections import Counter, defaultdict
from decimal import Decimal
from functools import lru_cache

from vouch import dates
from vouch.world import config as C
from vouch.world.build import generate

OPEN = C.OPEN_MONTH


@lru_cache(maxsize=1)
def full():
    result = generate()
    keys = {p.plant_id: p.key for p in result.plants + result.noise}
    return keys, result.bundle.files


def rows(files, path, **match):
    return [r for r in files[path] if all(r[k] == v for k, v in match.items())]


def person_subjects(keys, files):
    """Every employee each plant or noise item is about."""
    out = defaultdict(set)
    for plant_id, key in keys.items():
        subject = key["subject"]
        if subject.get("employee_id"):
            out[plant_id].add(subject["employee_id"])
        for line in subject.get("unpaid_lines", []):
            out[plant_id].add(line["employee_id"])
        if plant_id == "NOISE-03":
            payment = subject["payment_id"]
            out[plant_id].add(rows(files, f"months/{key['month']}/payment_file_DE.csv", payment_id=payment)[0]["employee_id"])
    return out


def test_no_two_cases_share_a_subject():
    keys, files = full()
    owners = Counter(e for people in person_subjects(keys, files).values() for e in people)
    assert [e for e, n in owners.items() if n > 1] == []
    grants = [k["subject"]["grant_id"] for k in keys.values() if "grant_id" in k["subject"]]
    assert len(grants) == len(set(grants)) == 2


def test_every_plant_still_holds_when_all_are_planted():
    keys, f = full()
    hr = {r["employee_id"]: r for r in f[f"months/{OPEN}/hr_census.csv"]}

    s = keys["MAIN-01"]["subject"]
    assert rows(f, f"months/{OPEN}/payment_file_{s['entity']}.csv", payment_id=s["payment_id"])[0]["bank_account"] \
        == s["requested_bank_account"] != hr[s["employee_id"]]["bank_account"]
    assert any(e["email_id"] == s["email_id"] for e in f["standing/inbox.json"]["emails"])

    s, paid = keys["MAIN-02"]["subject"], keys["MAIN-02"]["month"]
    assert rows(f, f"months/{paid}/payroll_register_{s['entity']}.csv", employee_id=s["employee_id"])
    assert hr[s["employee_id"]]["exit_date"] < dates.month_start(paid).isoformat()

    s = keys["MAIN-03"]["subject"]
    assert rows(f, f"months/{OPEN}/payroll_register_{s['entity']}.csv", employee_id=s["employee_id"])
    assert hr[s["employee_id"]]["status"] == "left"

    s = keys["MAIN-04"]["subject"]
    assert rows(f, f"months/{OPEN}/equity_report.csv", grant_id=s["grant_id"])[0]["status"] == "active"
    assert hr[s["employee_id"]]["status"] == "left"

    s = keys["MAIN-05"]["subject"]
    grant = rows(f, f"months/{OPEN}/equity_report.csv", grant_id=s["grant_id"])[0]
    assert grant["status"] == "active" and grant["board_resolution"] == "" and Decimal(grant["expense_month_usd"]) > 0

    assert {r["employer_pension_rate"] for r in f[f"months/{keys['MAIN-06']['month']}/payroll_register_UK.csv"]} == {"0.03"}

    s = keys["MAIN-07"]["subject"]
    assert rows(f, f"months/{OPEN}/payroll_register_{s['entity']}.csv", employee_id=s["employee_id"])
    assert s["employee_id"] not in hr

    s = keys["MAIN-08"]["subject"]
    assert rows(f, "standing/salary_changes.csv", change_id=s["change_id"])
    assert rows(f, f"months/{OPEN}/payroll_register_{s['entity']}.csv",
                employee_id=s["employee_id"])[0]["annual_salary"] == s["new_salary"]

    s, run = keys["MAIN-09"]["subject"], keys["MAIN-09"]["month"]
    bank = {b["reference"] for b in f[f"months/{run}/bank_statement_{s['entity']}.csv"]}
    assert not {u["payment_id"] for u in s["unpaid_lines"]} & bank
    assert any(r.startswith("PAY-") for r in bank)

    s, run = keys["MAIN-10"]["subject"], keys["MAIN-10"]["month"]
    assert s["payment_id"] not in {b["reference"] for b in f[f"months/{run}/bank_statement_US.csv"]}
    assert rows(f, f"months/{run}/ledger_booked.csv", entry_id=s["entry_id"])

    s = keys["MAIN-11"]["subject"]
    assert len(rows(f, f"months/{OPEN}/payment_file_{s['entity']}.csv", employee_id=s["employee_id"])) == 2

    s = keys["MAIN-12"]["subject"]
    assert hr[s["employee_id"]]["entity"] == s["hr_entity"]
    assert rows(f, f"months/{OPEN}/payroll_register_{s['payroll_entity']}.csv", employee_id=s["employee_id"])

    s = keys["MAIN-13"]["subject"]
    (request,) = rows(f, "standing/approvals_log.csv", request_id=s["request_id"])
    assert request["requested_by"] == request["approved_by"]

    s = keys["MAIN-14"]["subject"]
    assert hr[s["employee_id"]]["bank_account"] == s["bank_account"]
    assert rows(f, f"months/{OPEN}/payment_file_{s['entity']}.csv",
                employee_id=s["employee_id"])[0]["bank_account"] == s["bank_account"]

    assert Counter(r["employee_id"] for r in f[f"months/{OPEN}/hr_census.csv"])[
        keys["NOISE-02"]["subject"]["employee_id"]] == 2
    s, run = keys["NOISE-03"]["subject"], keys["NOISE-03"]["month"]
    assert rows(f, f"months/{dates.add_months(run, 1)}/bank_statement_DE.csv", reference=s["payment_id"])
    assert {r["fx_rate"] for r in rows(f, f"months/{OPEN}/equity_report.csv", entity="DE")} == \
        {keys["NOISE-04"]["subject"]["platform_rate"]}


def test_every_booked_entry_balances_with_everything_planted():
    _, f = full()
    for key in dates.month_range(C.FIRST_FILE_MONTH, OPEN):
        totals = defaultdict(Decimal)
        for line in f[f"months/{key}/ledger_booked.csv"]:
            totals[line["entry_id"]] += Decimal(line["debit"]) - Decimal(line["credit"])
        assert not [e for e, v in totals.items() if v], key
