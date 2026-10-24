"""The one pay function. The generator, the agent's tools and the checks all call
calculate_pay; nothing else computes withholdings or employer costs."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from vouch import dates
from vouch.calc.rates import dec, rate
from vouch.money import ZERO, cents, cents_down, whole_down

# How each component posts to the ledger.
COMPONENT_KIND = {
    "federal_income_tax": "income_tax", "paye_income_tax": "income_tax",
    "lohnsteuer": "income_tax", "solidaritaetszuschlag": "income_tax",
    "employee_social_security": "employee_social", "employee_medicare": "employee_social",
    "employee_additional_medicare": "employee_social", "employee_ni": "employee_social",
    "employee_rv": "employee_social", "employee_av": "employee_social",
    "employee_kv": "employee_social", "employee_pv": "employee_social",
    "employer_social_security": "employer_social", "employer_medicare": "employer_social",
    "employer_futa": "employer_social", "employer_ni": "employer_social",
    "employer_rv": "employer_social", "employer_av": "employer_social",
    "employer_kv": "employer_social", "employer_pv": "employer_social",
    "employer_pension": "employer_pension",
}

EMPLOYEE_COMPONENTS = {
    "US": ["federal_income_tax", "employee_social_security", "employee_medicare", "employee_additional_medicare"],
    "UK": ["paye_income_tax", "employee_ni"],
    "DE": ["lohnsteuer", "solidaritaetszuschlag", "employee_rv", "employee_av", "employee_kv", "employee_pv"],
}
EMPLOYER_COMPONENTS = {
    "US": ["employer_social_security", "employer_medicare", "employer_futa"],
    "UK": ["employer_ni", "employer_pension"],
    "DE": ["employer_rv", "employer_av", "employer_kv", "employer_pv"],
}
# Year-to-date bases each entity tracks (all amounts except sv_days, which is a day count).
YTD_KEYS = {
    "US": ["gross", "ss_wages", "medicare_wages", "futa_wages"],
    "UK": ["gross"],
    "DE": ["gross", "rv_base", "kv_base", "sv_days"],
}


@dataclass(frozen=True)
class PayResult:
    entity: str
    tax_year: int
    period: int
    gross: Decimal
    employee: dict[str, Decimal]
    employer: dict[str, Decimal]
    net: Decimal
    bases: dict[str, Decimal]          # this period's bases; add to year_to_date for the next period
    citations: list[str] = field(default_factory=list)

    @property
    def employee_total(self) -> Decimal:
        return sum(self.employee.values(), ZERO)

    @property
    def employer_total(self) -> Decimal:
        return sum(self.employer.values(), ZERO)


def tax_period(entity: str, month: str) -> tuple[int, int]:
    """(tax year, period number) for a monthly payroll paid in `month`."""
    year, m = dates.parse_month(month)
    if entity == "UK":       # tax year starts 6 April; April's payroll is month 1
        return (year, m - 3) if m >= 4 else (year - 1, m + 9)
    return year, m


def calculate_pay(entity: str, year: int, period: int, gross: Decimal, year_to_date: Mapping[str, Decimal],
                  *, employer_pension_rate: Decimal | None = None,
                  days: tuple[int, int] | None = None) -> PayResult:
    """Withholdings, net pay and employer costs for one person, one entity, one monthly period.

    year, period   tax year (UK: the starting year) and period number, see tax_period()
    gross          gross pay for the period, already prorated
    year_to_date   bases before this period, keys from YTD_KEYS (missing keys count as zero)
    employer_pension_rate  company pension rate from the pay policy (UK only)
    days           (days employed, days in month) when the period is partial; DE uses it
    """
    if gross < 0:
        raise ValueError("gross pay cannot be negative")
    ytd = {k: Decimal(year_to_date.get(k, 0)) for k in YTD_KEYS[entity]}
    if entity == "US":
        return _us(year, period, gross, ytd)
    if entity == "UK":
        if employer_pension_rate is None:
            raise ValueError("UK pay needs the employer pension rate from the pay policy")
        return _uk(year, period, gross, Decimal(employer_pension_rate))
    if entity == "DE":
        return _de(year, period, gross, ytd, days)
    raise ValueError(f"unknown entity {entity}")


def _capped(gross: Decimal, ceiling: Decimal, used: Decimal) -> Decimal:
    return max(ZERO, min(gross, ceiling - used))


# -- United States -----------------------------------------------------------
def _us(year: int, period: int, gross: Decimal, ytd: dict) -> PayResult:
    annual = gross * 12
    adjusted = max(ZERO, annual - dec("US", year, "withholding_line_1g"))
    schedule = rate("US", year, "withholding_schedule")
    row = [r for r in schedule.value if adjusted >= Decimal(r[0])][-1]
    tentative = Decimal(row[1]) + (adjusted - Decimal(row[0])) * Decimal(row[2])
    fit = cents(tentative / 12)

    ss_rate, med_rate = dec("US", year, "social_security_rate"), dec("US", year, "medicare_rate")
    ss_wages = _capped(gross, dec("US", year, "social_security_wage_base"), ytd["ss_wages"])
    threshold = dec("US", year, "additional_medicare_threshold")
    over = max(ZERO, ytd["medicare_wages"] + gross - threshold) - max(ZERO, ytd["medicare_wages"] - threshold)
    futa_wages = _capped(gross, dec("US", year, "futa_wage_base"), ytd["futa_wages"])

    employee = {
        "federal_income_tax": fit,
        "employee_social_security": cents(ss_wages * ss_rate),
        "employee_medicare": cents(gross * med_rate),
        "employee_additional_medicare": cents(over * dec("US", year, "additional_medicare_rate")),
    }
    employer = {
        "employer_social_security": cents(ss_wages * ss_rate),
        "employer_medicare": cents(gross * med_rate),
        "employer_futa": cents(futa_wages * dec("US", year, "futa_rate")),
    }
    names = ["withholding_line_1g", "withholding_schedule", "social_security_rate", "social_security_wage_base",
             "medicare_rate", "additional_medicare_rate", "additional_medicare_threshold", "futa_rate", "futa_wage_base"]
    return PayResult("US", year, period, gross, employee, employer, gross - sum(employee.values(), ZERO),
                     {"gross": gross, "ss_wages": ss_wages, "medicare_wages": gross, "futa_wages": futa_wages},
                     [rate("US", year, n).citation for n in names])


# -- United Kingdom ----------------------------------------------------------
def _uk(year: int, period: int, gross: Decimal, pension_rate: Decimal) -> PayResult:
    code = dec("UK", year, "tax_code_number")
    free_pay = (code * 10 + 9) / 12                     # HMRC pay adjustment for an L code, monthly
    taxable = max(ZERO, gross - free_pay)
    basic_limit = dec("UK", year, "basic_rate_band") / 12
    higher_limit = (dec("UK", year, "higher_rate_limit") - code * 10) / 12
    tax = (min(taxable, basic_limit) * dec("UK", year, "basic_rate")
           + max(ZERO, min(taxable, higher_limit) - basic_limit) * dec("UK", year, "higher_rate")
           + max(ZERO, taxable - higher_limit) * dec("UK", year, "additional_rate"))

    pt, uel = dec("UK", year, "ni_primary_threshold_monthly"), dec("UK", year, "ni_upper_earnings_limit_monthly")
    st = dec("UK", year, "ni_secondary_threshold_monthly")
    ee_ni = (max(ZERO, min(gross, uel) - pt) * dec("UK", year, "ni_employee_main_rate")
             + max(ZERO, gross - uel) * dec("UK", year, "ni_employee_upper_rate"))
    er_ni = max(ZERO, gross - st) * dec("UK", year, "ni_employer_rate")

    employee = {"paye_income_tax": cents_down(tax), "employee_ni": cents(ee_ni)}
    employer = {"employer_ni": cents(er_ni), "employer_pension": cents(gross * pension_rate)}
    names = ["tax_code_number", "basic_rate", "basic_rate_band", "higher_rate", "higher_rate_limit", "additional_rate",
             "ni_primary_threshold_monthly", "ni_upper_earnings_limit_monthly", "ni_secondary_threshold_monthly",
             "ni_employee_main_rate", "ni_employee_upper_rate", "ni_employer_rate"]
    return PayResult("UK", year, period, gross, employee, employer, gross - sum(employee.values(), ZERO),
                     {"gross": gross}, [rate("UK", year, n).citation for n in names] + ["Pay policy PAY-04"])


# -- Germany -----------------------------------------------------------------
def income_tax_de(year: int, taxable_income: Decimal) -> Decimal:
    """§ 32a EStG tariff on a whole-euro taxable income; result rounded down to whole euro."""
    t = {k: Decimal(v) for k, v in rate("DE", year, "income_tax_tariff").value.items()}
    x = whole_down(taxable_income)
    if x <= t["basic_allowance"]:
        tax = ZERO
    elif x <= t["zone2_end"]:
        y = (x - t["basic_allowance"]) / 10000
        tax = (t["zone2_a"] * y + t["zone2_b"]) * y
    elif x <= t["zone3_end"]:
        z = (x - t["zone2_end"]) / 10000
        tax = (t["zone3_a"] * z + t["zone3_b"]) * z + t["zone3_c"]
    elif x <= t["zone4_end"]:
        tax = t["zone4_rate"] * x - t["zone4_minus"]
    else:
        tax = t["zone5_rate"] * x - t["zone5_minus"]
    return whole_down(tax)


def _de(year: int, period: int, gross: Decimal, ytd: dict, days: tuple[int, int] | None) -> PayResult:
    employed, in_month = days or (1, 1)
    sv_days = Decimal(30 if employed >= in_month else min(employed, 30))
    rv_ceiling = dec("DE", year, "pension_unemployment_ceiling")
    kv_ceiling = dec("DE", year, "health_care_ceiling")
    # Pro-rata annual ceiling by social-insurance days (30 per full month, 360 per year).
    rv_base = _capped(gross, cents(rv_ceiling * (ytd["sv_days"] + sv_days) / 360), ytd["rv_base"])
    kv_base = _capped(gross, cents(kv_ceiling * (ytd["sv_days"] + sv_days) / 360), ytd["kv_base"])

    def half(name: str, base: Decimal) -> Decimal:
        return cents(base * dec("DE", year, name) / 2)

    social = {"rv": half("pension_rate", rv_base), "av": half("unemployment_rate", rv_base),
              "kv": half("health_rate", kv_base), "pv": half("care_rate", kv_base)}

    # Wage tax: annualise the period by social-insurance days, apply the tariff, scale back.
    scale = Decimal(360) / sv_days
    pre_tax = gross * scale - dec("DE", year, "employee_lump_sum") - dec("DE", year, "special_expenses_lump_sum") \
        - (social["rv"] + social["kv"] + social["pv"]) * scale
    annual_tax = income_tax_de(year, max(ZERO, pre_tax))
    limit = dec("DE", year, "solidarity_exemption_limit")
    annual_soli = ZERO if annual_tax <= limit else min(annual_tax * dec("DE", year, "solidarity_rate"),
                                                      (annual_tax - limit) * dec("DE", year, "solidarity_taper_rate"))
    employee = {
        "lohnsteuer": cents_down(annual_tax / scale),
        "solidaritaetszuschlag": cents_down(annual_soli / scale),
        **{f"employee_{k}": v for k, v in social.items()},
    }
    employer = {f"employer_{k}": v for k, v in social.items()}
    names = ["income_tax_tariff", "employee_lump_sum", "special_expenses_lump_sum", "solidarity_rate",
             "solidarity_exemption_limit", "solidarity_taper_rate", "pension_rate", "unemployment_rate",
             "health_rate", "care_rate", "pension_unemployment_ceiling", "health_care_ceiling"]
    return PayResult("DE", year, period, gross, employee, employer, gross - sum(employee.values(), ZERO),
                     {"gross": gross, "rv_base": rv_base, "kv_base": kv_base, "sv_days": sv_days},
                     [rate("DE", year, n).citation for n in names])
