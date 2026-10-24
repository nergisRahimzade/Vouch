"""W2: every amount in the files comes from the Layer 3 engine, never typed."""
from collections import defaultdict
from decimal import Decimal

import pytest

from vouch.calc import fx, rates
from vouch.calc.pay import EMPLOYEE_COMPONENTS, EMPLOYER_COMPONENTS, YTD_KEYS, calculate_pay
from vouch.money import cents

ENTITIES = ("US", "UK", "DE")


def _ytd_before(row, entity, previous):
    """Year-to-date before this period: the previous row's total, or this row's total less its period base."""
    if previous is not None:
        return {k: Decimal(previous[f"ytd_{k}"]) for k in YTD_KEYS[entity]}
    period = {k: Decimal(row["gross_pay"] if k == "gross" else row[f"period_{k}"]) for k in YTD_KEYS[entity]}
    return {k: Decimal(row[f"ytd_{k}"]) - period[k] for k in YTD_KEYS[entity]}


def _engine(row, entity, ytd):
    return calculate_pay(entity, int(row["tax_year"]), int(row["tax_period"]), Decimal(row["gross_pay"]), ytd,
                         employer_pension_rate=Decimal(row["employer_pension_rate"]) if entity == "UK" else None,
                         days=(int(row["days_paid"]), int(row["days_in_month"])))


def test_every_register_row_equals_the_engine(world):
    checked = 0
    for entity in ENTITIES:
        last = {}
        for month in world.months():
            for row in world.csv(f"months/{month}/payroll_register_{entity}.csv"):
                annual, days, dim = Decimal(row["annual_salary"]), int(row["days_paid"]), int(row["days_in_month"])
                expected_gross = cents(annual / 12) if days >= dim else cents(annual / 12 * days / dim)
                assert Decimal(row["gross_pay"]) == expected_gross, row
                key = (row["employee_id"], row["tax_year"])
                result = _engine(row, entity, _ytd_before(row, entity, last.get(key)))
                for component in EMPLOYEE_COMPONENTS[entity]:
                    assert Decimal(row[component]) == result.employee[component], (component, row)
                for component in EMPLOYER_COMPONENTS[entity]:
                    assert Decimal(row[component]) == result.employer[component], (component, row)
                assert Decimal(row["net_pay"]) == result.net, row
                for k in YTD_KEYS[entity]:
                    if k != "gross":
                        assert Decimal(row[f"period_{k}"]) == result.bases[k], (k, row)
                last[key] = row
                checked += 1
    assert checked > 3000


def test_payments_carry_register_net_pay(world):
    for month in world.months():
        for entity in ENTITIES:
            net = {r["employee_id"]: r["net_pay"] for r in world.csv(f"months/{month}/payroll_register_{entity}.csv")}
            for payment in world.csv(f"months/{month}/payment_file_{entity}.csv"):
                assert payment["amount"] == net[payment["employee_id"]]


def test_booked_wages_equal_register_gross_by_department(world):
    for month in world.months()[:-1]:
        ledger = world.csv(f"months/{month}/ledger_booked.csv")
        for entity in ENTITIES:
            register = defaultdict(Decimal)
            for r in world.csv(f"months/{month}/payroll_register_{entity}.csv"):
                register[r["department"]] += Decimal(r["gross_pay"])
            booked = defaultdict(Decimal)
            for line in ledger:
                if line["entity"] == entity and line["account"] == "6000":
                    booked[line["department"]] += Decimal(line["debit"]) - Decimal(line["credit"])
            assert booked == register


def test_booked_stock_comp_is_equity_report_at_month_average_rate(world):
    for month in world.months()[:-1]:
        ledger = world.csv(f"months/{month}/ledger_booked.csv")
        for entity, currency in zip(ENTITIES, ("USD", "GBP", "EUR")):
            rate = fx.month_average(currency, month)
            expected = sum((fx.to_local(Decimal(r["expense_month_usd"]), rate)
                            for r in world.csv(f"months/{month}/equity_report.csv") if r["entity"] == entity), Decimal(0))
            booked = sum((Decimal(l["debit"]) - Decimal(l["credit"]) for l in ledger
                          if l["entity"] == entity and l["account"] == "6300"), Decimal(0))
            assert booked == expected


def test_every_rate_has_a_source_and_a_year():
    for entity in ENTITIES:
        years = rates.tax_years(entity)
        assert years, entity
        for year in years:
            for name, entry in rates._table()[entity][str(year)].items():
                assert entry["source"].startswith("https://"), (entity, year, name)
                assert entry["checked"] in ("official-document", "secondary-confirmed", "recalled")


def test_month_with_missing_business_days_has_no_rate():
    with pytest.raises(fx.MissingFxRate):
        fx.month_average("GBP", "2030-01")
    assert fx.month_average("EUR", "2026-09").days == len(fx.business_days("2026-09"))
