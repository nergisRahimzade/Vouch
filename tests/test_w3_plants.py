"""W3: each planted mistake changes exactly what it says, compared with a clean build."""
from decimal import Decimal
from functools import lru_cache

import pytest

from vouch import dates
from vouch.world import config as C
from vouch.world.build import generate
from vouch.world.plants import MAIN_SET
from worlddiff import diff

OPEN = C.OPEN_MONTH


def m(offset):
    return dates.add_months(OPEN, offset)


@lru_cache(maxsize=1)
def clean():
    return generate(clean=True, noise=False).bundle.files


@lru_cache(maxsize=None)
def planted(plant_id):
    result = generate(plants=[plant_id], noise=False)
    return result.plants[0].key, diff(clean(), result.bundle.files)


def only_changed(fields: dict, allowed: set):
    assert set(fields) <= allowed, fields


def test_bank_change_by_email():
    key, d = planted("MAIN-01")
    e, s = key["subject"]["entity"], key["subject"]
    assert set(d) == {f"months/{OPEN}/payment_file_{e}.csv", "standing/inbox.json"}
    change = d[f"months/{OPEN}/payment_file_{e}.csv"]
    assert not change["added"] and not change["removed"]
    assert change["changed"] == {s["payment_id"]: {"bank_account": (s["hr_bank_account"], s["requested_bank_account"])}}
    assert len(generate(plants=["MAIN-01"], noise=False).bundle.files["standing/inbox.json"]["emails"]) == \
        len(clean()["standing/inbox.json"]["emails"]) + 1


def test_leaver_still_paid():
    key, d = planted("MAIN-02")
    e, who = key["subject"]["entity"], key["subject"]["employee_id"]
    paid = m(-1)
    assert set(d) == {f"months/{paid}/payroll_register_{e}.csv", f"months/{paid}/payment_file_{e}.csv",
                      f"months/{paid}/bank_statement_{e}.csv", f"months/{paid}/ledger_booked.csv",
                      f"months/{OPEN}/bank_statement_{e}.csv", f"months/{OPEN}/ledger_booked.csv"}
    register = d[f"months/{paid}/payroll_register_{e}.csv"]
    assert [r["employee_id"] for r in register["added"]] == [who] and not register["changed"]
    assert register["added"][0]["gross_pay"] == key["expected_amount"]["value"]
    assert [r["employee_id"] for r in d[f"months/{paid}/payment_file_{e}.csv"]["added"]] == [who]
    assert dates.month_of(dates.parse_date(key["subject"]["exit_date"])) < paid


def test_leaver_paid_years_later():
    key, d = planted("MAIN-03")
    e, who = key["subject"]["entity"], key["subject"]["employee_id"]
    assert set(d) == {f"months/{OPEN}/payroll_register_{e}.csv", f"months/{OPEN}/payment_file_{e}.csv"}
    assert [r["employee_id"] for r in d[f"months/{OPEN}/payroll_register_{e}.csv"]["added"]] == [who]
    payment = d[f"months/{OPEN}/payment_file_{e}.csv"]["added"]
    assert len(payment) == 1 and payment[0]["amount"] == key["expected_amount"]["value"]
    assert key["subject"]["exit_date"] < m(-24)


def test_forfeited_grant_still_expensed():
    key, d = planted("MAIN-04")
    grant = key["subject"]["grant_id"]
    equity = {f"months/{k}/equity_report.csv" for k in dates.month_range(m(-3), OPEN)}
    ledgers = {f"months/{k}/ledger_booked.csv" for k in dates.month_range(m(-3), m(-1))}
    assert set(d) == equity | ledgers
    for path in equity:
        assert list(d[path]["changed"]) == [grant] and not d[path]["added"] and not d[path]["removed"]
        assert d[path]["changed"][grant]["status"][1] == "active"
    for path in ledgers:
        assert {k[1] for k in d[path]["changed"]} <= {"6300", "3100"}
    cumulative = Decimal(key["subject"]["platform_months"][m(-1)]["cumulative_usd"])
    assert key["expected_amount"]["value"] == f"{cumulative:.2f}" and cumulative > 0


def test_grant_without_board_approval():
    key, d = planted("MAIN-05")
    assert set(d) == {f"months/{OPEN}/equity_report.csv"}
    change = d[f"months/{OPEN}/equity_report.csv"]["changed"][key["subject"]["grant_id"]]
    assert change["status"] == ("pending_approval", "active")
    assert change["expense_month_usd"][1] == key["expected_amount"]["value"]
    only_changed(change, {"status", "grant_date", "fair_value_per_share_usd", "expense_month_usd",
                          "cumulative_expense_usd", "expense_month_local"})
    after = generate(plants=["MAIN-05"], noise=False).bundle.files[f"months/{OPEN}/equity_report.csv"]
    assert next(r for r in after if r["grant_id"] == key["subject"]["grant_id"])["board_resolution"] == ""


def test_wrong_pension_rate():
    key, d = planted("MAIN-06")
    run = m(-4)
    assert set(d) == {f"months/{run}/payroll_register_UK.csv", f"months/{run}/ledger_booked.csv",
                      f"months/{dates.add_months(run, 1)}/bank_statement_UK.csv",
                      f"months/{dates.add_months(run, 1)}/ledger_booked.csv"}
    register = d[f"months/{run}/payroll_register_UK.csv"]
    assert len(register["changed"]) == key["subject"]["rows"]
    shortfall = Decimal(0)
    for fields in register["changed"].values():
        only_changed(fields, {"employer_pension", "employer_pension_rate"})
        shortfall += Decimal(fields["employer_pension"][0]) - Decimal(fields["employer_pension"][1])
    assert f"{shortfall:.2f}" == key["expected_amount"]["value"]


def test_person_not_in_hr():
    key, d = planted("MAIN-07")
    e, who = key["subject"]["entity"], key["subject"]["employee_id"]
    assert set(d) == {f"months/{OPEN}/payroll_register_{e}.csv", f"months/{OPEN}/payment_file_{e}.csv"}
    assert who not in {r["employee_id"] for r in clean()[f"months/{OPEN}/hr_census.csv"]}


def test_backdated_raise():
    key, d = planted("MAIN-08")
    e, who = key["subject"]["entity"], key["subject"]["employee_id"]
    assert set(d) == {"standing/salary_changes.csv", f"months/{OPEN}/payroll_register_{e}.csv",
                      f"months/{OPEN}/payment_file_{e}.csv"}
    added = d["standing/salary_changes.csv"]["added"]
    assert len(added) == 1 and added[0]["effective_date"] < dates.month_start(OPEN).isoformat()
    assert added[0]["approved_on"] >= dates.month_start(OPEN).isoformat()
    assert list(d[f"months/{OPEN}/payroll_register_{e}.csv"]["changed"]) == [who]
    (payment,) = d[f"months/{OPEN}/payment_file_{e}.csv"]["changed"].values()
    only_changed(payment, {"amount"})
    assert Decimal(payment["amount"][1]) > Decimal(payment["amount"][0])


def test_half_cleared_batch():
    key, d = planted("MAIN-09")
    e, run = key["subject"]["entity"], m(-1)
    assert set(d) == {f"months/{run}/bank_statement_{e}.csv", f"months/{run}/ledger_booked.csv"}
    bank = d[f"months/{run}/bank_statement_{e}.csv"]
    assert sorted(r["reference"] for r in bank["removed"]) == sorted(key["subject"]["unpaid_payment_ids"])
    assert not bank["added"]
    for fields in bank["changed"].values():
        only_changed(fields, {"bank_line_id"})
    salaries = [r for r in clean()[f"months/{run}/bank_statement_{e}.csv"] if r["reference"].startswith("PAY-")]
    assert key["subject"]["lines_in_batch"] == len(salaries)
    assert len(bank["removed"]) == key["subject"]["lines_eligible"] // 2


def test_booked_but_never_cleared():
    key, d = planted("MAIN-10")
    run = m(-2)
    assert set(d) == {f"months/{run}/bank_statement_US.csv", f"months/{run}/ledger_booked.csv"}
    assert [r["reference"] for r in d[f"months/{run}/bank_statement_US.csv"]["removed"]] == [key["subject"]["payment_id"]]
    manual = [r for r in d[f"months/{run}/ledger_booked.csv"]["added"] if r["entry_id"] == key["subject"]["entry_id"]]
    assert {r["account"] for r in manual} == {"1000", "2100"}


def test_duplicate_payment():
    key, d = planted("MAIN-11")
    e = key["subject"]["entity"]
    assert set(d) == {f"months/{OPEN}/payment_file_{e}.csv"}
    added = d[f"months/{OPEN}/payment_file_{e}.csv"]["added"]
    assert len(added) == 1 and added[0]["employee_id"] == key["subject"]["employee_id"]


def test_transfer_not_reflected():
    key, d = planted("MAIN-12")
    assert set(d) == {f"months/{OPEN}/hr_census.csv"}
    change = d[f"months/{OPEN}/hr_census.csv"]["changed"]
    assert list(change) == [key["subject"]["employee_id"]]
    only_changed(change[key["subject"]["employee_id"]], {"entity", "entity_since", "bank_account"})


def test_self_approved_accrual():
    key, d = planted("MAIN-13")
    assert set(d) == {"standing/approvals_log.csv"}
    (row,) = d["standing/approvals_log.csv"]["added"]
    assert row["requested_by"] == row["approved_by"] and Decimal(row["amount"]) >= Decimal("40000")


def test_new_hire_with_leavers_account():
    key, d = planted("MAIN-14")
    e, who = key["subject"]["entity"], key["subject"]["employee_id"]
    assert set(d) == {f"months/{OPEN}/hr_census.csv", f"months/{OPEN}/payment_file_{e}.csv"}
    assert d[f"months/{OPEN}/hr_census.csv"]["changed"][who]["bank_account"][1] == key["subject"]["bank_account"]
    history = clean()["standing/legacy_payment_history.csv"]
    assert any(h["bank_account"] == key["subject"]["bank_account"]
               and h["employee_id"] == key["subject"]["previous_holder_employee_id"] for h in history)


@pytest.mark.parametrize("plant_id", [p for p, _ in MAIN_SET])
def test_every_plant_has_a_complete_key(plant_id):
    key, _ = planted(plant_id)
    for field in ("plant_id", "kind", "month", "subject", "sources_disagree", "trusted_source", "expected_outcome",
                  "expected_behaviour", "ask", "rules", "expected_amount"):
        assert field in key, field
    if key["expected_amount"]:
        assert key["expected_amount"]["verification"] in (
            "copied-from-source", "independent-arithmetic", "engine-output-pending-hand-check")


def test_full_build_plants_all_fourteen_some_open_some_closed():
    plants = generate().plants
    assert len(plants) == len(MAIN_SET) == 14
    months = {p.key["month"] for p in plants}
    assert OPEN in months and any(k < OPEN for k in months)
    subjects = [p.key["subject"].get("employee_id") for p in plants if p.key["subject"].get("employee_id")]
    assert len(subjects) == len(set(subjects))


@pytest.mark.parametrize("seed", range(C.SEED, C.SEED + 20))
def test_every_seed_has_a_subject_for_every_plant(seed):
    """The world casts the people each plant needs, so no plant depends on luck."""
    result = generate(seed=seed)
    assert [p.plant_id for p in result.plants] == [p for p, _ in MAIN_SET]
    assert all(p.key.get("subject") for p in result.plants)
