"""Builds each month's money files in the order a real close receives them:

    hr census -> payroll register -> payment file -> equity report -> bank statement -> booked ledger

Each stage reads only the stages before it, so a mistake planted at one stage flows
downstream the way it would in a real company. Hooks (planted mistakes, noise) run
after their stage. Every amount comes from vouch.calc; nothing is typed."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable

from vouch import dates
from vouch.calc import fx, stock
from vouch.calc.pay import (COMPONENT_KIND, EMPLOYEE_COMPONENTS, EMPLOYER_COMPONENTS, YTD_KEYS, calculate_pay,
                            tax_period)
from vouch.money import ZERO, cents, fmt
from vouch.world import config as C
from vouch.world.documents import FIELDS, Bundle, hr_census
from vouch.world.policies import PAY_POLICY
from vouch.world.simulate import Person, World

STAGES = ("hr", "register", "payments", "equity", "bank", "ledger")

COMMON_REGISTER = ["period", "entity", "currency", "employee_id", "employee_name", "department", "level",
                   "pay_date", "days_paid", "days_in_month", "annual_salary", "gross_pay", "tax_year", "tax_period"]
for _entity in C.ENTITIES:
    FIELDS[f"payroll_register_{_entity}.csv"] = (
        COMMON_REGISTER + EMPLOYEE_COMPONENTS[_entity] + ["net_pay"] + EMPLOYER_COMPONENTS[_entity]
        + (["employer_pension_rate"] if _entity == "UK" else [])
        + [f"period_{k}" for k in YTD_KEYS[_entity] if k != "gross"] + [f"ytd_{k}" for k in YTD_KEYS[_entity]])
    FIELDS[f"payment_file_{_entity}.csv"] = ["payment_id", "batch_id", "entity", "employee_id", "payee_name",
                                            "bank_account", "amount", "currency", "value_date", "status"]
    FIELDS[f"bank_statement_{_entity}.csv"] = ["bank_line_id", "entity", "account_name", "date", "description",
                                              "reference", "amount", "currency"]
FIELDS["equity_report.csv"] = ["grant_id", "employee_id", "entity", "department", "grant_date", "vest_start",
                               "shares", "fair_value_per_share_usd", "board_resolution", "status", "service_months",
                               "vested_shares", "expense_month_usd", "cumulative_expense_usd", "local_currency",
                               "fx_rate", "fx_rate_basis", "expense_month_local"]
FIELDS["opening_balances.csv"] = ["entity", "as_of", "account", "account_name", "debit", "credit", "currency",
                                  "fx_rate", "basis"]
FIELDS["ledger_booked.csv"] = ["entry_id", "entity", "period", "date", "account", "account_name", "department",
                               "debit", "credit", "currency", "fx_rate", "fx_rate_basis", "memo", "source_ref",
                               "booked_by"]

ACCOUNT_NAMES = {
    "1000": "Cash at bank", "2100": "Net pay payable", "2200": "Income tax withholding payable",
    "2210": "Social contributions payable", "2220": "Pension contributions payable",
    "3100": "Additional paid-in capital", "3200": "Retained earnings", "6000": "Wages and salaries", "6100": "Employer payroll taxes",
    "6200": "Employer pension", "6300": "Stock-based compensation", "9999": "Suspense",
}
LIABILITY_FOR_KIND = {"income_tax": "2200", "employee_social": "2210", "employer_social": "2210",
                      "employer_pension": "2220"}
EXPENSE_FOR_KIND = {"employer_social": "6100", "employer_pension": "6200"}

# Who each entity remits to on the 15th of the following month, and which components.
AUTHORITIES = {
    "US": [("IRS EFTPS FORM 941 DEPOSIT", "941", lambda c: c not in ("employer_futa",)),
           ("IRS EFTPS FORM 940 FUTA DEPOSIT", "940", lambda c: c == "employer_futa")],
    "UK": [("HMRC PAYE AND NIC", "PAYE", lambda c: c != "employer_pension"),
           ("PENSION PROVIDER CONTRIBUTIONS", "PENS", lambda c: c == "employer_pension")],
    "DE": [("FINANZAMT LOHNSTEUER", "LST", lambda c: COMPONENT_KIND[c] == "income_tax"),
           ("KRANKENKASSE SV-BEITRAEGE", "SV", lambda c: COMPONENT_KIND[c] != "income_tax")],
}
BOOKED_BY = {"payroll": "Daniel Reyes", "cash": "Lena Fischer", "stock": "Daniel Reyes", "opening": "Amara Okafor"}
OPENING_FILE = "standing/opening_balances.csv"


def pension_rate(entity: str) -> Decimal | None:
    rule = next(r for r in PAY_POLICY["rules"] if r["rule_id"] == "PAY-04")
    value = rule["employer_pension_rate"].get(entity)
    return Decimal(value) if value else None


def payroll_start(entity: str) -> str:
    """First month of the tax year that contains the first file month (for year-to-date totals)."""
    _, period = tax_period(entity, C.FIRST_FILE_MONTH)
    return dates.add_months(C.FIRST_FILE_MONTH, 1 - period)


def is_closed(month: str) -> bool:
    return month < C.OPEN_MONTH


@dataclass
class MonthContext:
    """What a hook sees: the month, the truth, the files so far and the payroll master."""
    month: str
    world: World
    bundle: Bundle
    master: dict[tuple[str, str], str]           # (employee_id, entity) -> bank account on the payroll system
    state: "PipelineState"

    def path(self, name: str) -> str:
        return f"months/{self.month}/{name}"

    def rows(self, name: str) -> list[dict]:
        return self.bundle.rows(self.path(name))

    def ytd_before(self, entity: str, employee_id: str) -> dict[str, Decimal]:
        return dict(self.state.ytd.get((entity, employee_id, tax_period(entity, self.month)[0]), {}))


@dataclass
class PipelineState:
    ytd: dict[tuple[str, str, int], dict[str, Decimal]] = field(default_factory=dict)
    previous_register: dict[str, list[dict]] = field(default_factory=dict)
    bank_carry: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))


Hook = Callable[[MonthContext], None]


# -- register ----------------------------------------------------------------
def register_row(entity: str, month: str, employee_id: str, name: str, department: str, level: int,
                 annual_salary: Decimal, days_paid: int, ytd: dict[str, Decimal],
                 employer_pension_rate: Decimal | None = None) -> dict:
    """One payroll register row, computed by the Layer 3 engine."""
    dim = dates.days_in_month(month)
    monthly = annual_salary / 12
    gross = cents(monthly) if days_paid >= dim else cents(monthly * days_paid / dim)
    year, period = tax_period(entity, month)
    rate = employer_pension_rate if employer_pension_rate is not None else pension_rate(entity)
    result = calculate_pay(entity, year, period, gross, ytd, employer_pension_rate=rate, days=(days_paid, dim))
    row = {
        "period": month, "entity": entity, "currency": C.ENTITIES[entity]["currency"], "employee_id": employee_id,
        "employee_name": name, "department": department, "level": str(level),
        "pay_date": dates.last_business_day(month).isoformat(), "days_paid": str(days_paid),
        "days_in_month": str(dim), "annual_salary": fmt(annual_salary), "gross_pay": fmt(gross),
        "tax_year": str(year), "tax_period": str(period),
        **{k: fmt(v) for k, v in result.employee.items()}, "net_pay": fmt(result.net),
        **{k: fmt(v) for k, v in result.employer.items()},
    }
    if entity == "UK":
        row["employer_pension_rate"] = str(rate)
    def text(key: str, value: Decimal) -> str:
        return str(int(value)) if key == "sv_days" else fmt(value)

    for key in YTD_KEYS[entity]:
        if key != "gross":      # this period's taxable base for each capped contribution
            row[f"period_{key}"] = text(key, result.bases[key])
    for key in YTD_KEYS[entity]:
        row[f"ytd_{key}"] = text(key, Decimal(ytd.get(key, 0)) + result.bases[key])
    return row


def person_rows(person: Person, entity: str, month: str, ytd: dict[str, Decimal]) -> list[dict]:
    rows = []
    for stint in person.stints:
        if stint.entity != entity:
            continue
        span = dates.overlap_days(stint.start, stint.end, month)
        if span is None:
            continue
        days = (span[1] - span[0]).days + 1
        rows.append(register_row(entity, month, person.employee_id, person.name, person.department,
                                 person.level_on(span[0]), person.salary_on(span[0]).annual_salary, days, ytd))
    return rows


def build_register(ctx: MonthContext, entity: str) -> list[dict]:
    rows = []
    for person in ctx.world.people:
        rows.extend(person_rows(person, entity, ctx.month, ctx.ytd_before(entity, person.employee_id)))
    return rows


# -- payments ----------------------------------------------------------------
def build_payments(ctx: MonthContext, entity: str) -> list[dict]:
    rows, batch = [], f"BATCH-{ctx.month}-{entity}"
    for reg in ctx.rows(f"payroll_register_{entity}.csv"):
        account = ctx.master.get((reg["employee_id"], entity))
        if account is None or Decimal(reg["net_pay"]) <= 0:
            continue
        rows.append({
            "payment_id": f"PAY-{ctx.month}-{entity}-{len(rows) + 1:04d}", "batch_id": batch, "entity": entity,
            "employee_id": reg["employee_id"], "payee_name": reg["employee_name"], "bank_account": account,
            "amount": reg["net_pay"], "currency": reg["currency"], "value_date": reg["pay_date"],
            "status": "released" if is_closed(ctx.month) else "pending_release"})
    return rows


# -- equity ------------------------------------------------------------------
def build_equity(ctx: MonthContext) -> list[dict]:
    rows, end = [], dates.month_end(ctx.month)
    rates = {e: fx.month_average(v["currency"], ctx.month) for e, v in C.ENTITIES.items()}
    for grant in ctx.world.grants:
        person = ctx.world.person(grant.employee_id)
        if person.hire_date > end:
            continue
        on = person.exit_date if person.exit_date and person.exit_date <= end else end
        entity = person.stint_on(on).entity
        terms = stock.GrantTerms(grant.shares, grant.fair_value_per_share, grant.grant_date, grant.vest_start,
                                 grant.vest_months, grant.cliff_months)
        result = stock.month_expense(terms, person.exit_date, ctx.month)
        status = "cancelled" if grant.cancelled_on and grant.cancelled_on <= end else result.status
        rate = rates[entity]
        rows.append({
            "grant_id": grant.grant_id, "employee_id": grant.employee_id, "entity": entity,
            "department": person.department, "grant_date": grant.grant_date.isoformat() if grant.grant_date and grant.grant_date <= end else "",
            "vest_start": grant.vest_start.isoformat(), "shares": str(grant.shares),
            "fair_value_per_share_usd": f"{grant.fair_value_per_share:.2f}" if grant.grant_date and grant.grant_date <= end else "",
            "board_resolution": grant.board_resolution if grant.grant_date and grant.grant_date <= end else "",
            "status": status, "service_months": str(result.service_months), "vested_shares": str(result.vested_shares),
            "expense_month_usd": fmt(result.expense), "cumulative_expense_usd": fmt(result.cumulative),
            "local_currency": C.ENTITIES[entity]["currency"], "fx_rate": str(rate.rate),
            "fx_rate_basis": rate.citation, "expense_month_local": fmt(fx.to_local(result.expense, rate)),
        })
    return rows


# -- bank --------------------------------------------------------------------
def remittance_date(month: str) -> date:
    d = dates.month_start(month) + timedelta(days=14)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def remittance_split(entity: str, register: list[dict]) -> list[tuple[str, str, dict[str, Decimal]]]:
    """[(description, code, {liability account: amount})] owed from one month's register."""
    out = []
    components = EMPLOYEE_COMPONENTS[entity] + EMPLOYER_COMPONENTS[entity]
    for description, code, includes in AUTHORITIES[entity]:
        split: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for row in register:
            for component in components:
                if includes(component):
                    split[LIABILITY_FOR_KIND[COMPONENT_KIND[component]]] += Decimal(row[component])
        if sum(split.values(), ZERO) > 0:
            out.append((description, code, dict(sorted(split.items()))))
    return out


def build_bank(ctx: MonthContext, entity: str) -> list[dict]:
    lines = list(ctx.state.bank_carry.pop(entity, []))
    previous = ctx.state.previous_register.get(entity, [])
    paid_for = dates.add_months(ctx.month, -1)
    for description, code, split in remittance_split(entity, previous):
        lines.append({"date": remittance_date(ctx.month).isoformat(), "description": description,
                      "reference": f"REM-{paid_for}-{entity}-{code}", "amount": fmt(-sum(split.values(), ZERO))})
    if is_closed(ctx.month):
        for payment in ctx.rows(f"payment_file_{entity}.csv"):
            lines.append({"date": payment["value_date"], "description": f"SALARY {payment['payee_name'].upper()}",
                          "reference": payment["payment_id"], "amount": fmt(-Decimal(payment["amount"]))})
    currency = C.ENTITIES[entity]["currency"]
    return [{"bank_line_id": f"BNK-{entity}-{ctx.month}-{n:04d}", "entity": entity,
             "account_name": f"{C.ENTITIES[entity]['legal_name']} payroll account", **line, "currency": currency}
            for n, line in enumerate(sorted(lines, key=lambda l: (l["date"], l["reference"])), 1)]


# -- ledger ------------------------------------------------------------------
def _line(entry_id: str, entity: str, month: str, day: str, account: str, amount: Decimal, *, department: str = "",
          memo: str, source: str, booked_by: str, fx_rate: str = "", fx_basis: str = "") -> dict:
    """A ledger line; a positive amount is a debit, a negative one a credit."""
    return {"entry_id": entry_id, "entity": entity, "period": month, "date": day, "account": account,
            "account_name": ACCOUNT_NAMES[account], "department": department,
            "debit": fmt(amount) if amount > 0 else "0.00", "credit": fmt(-amount) if amount < 0 else "0.00",
            "currency": C.ENTITIES[entity]["currency"], "fx_rate": fx_rate,
            "fx_rate_basis": fx_basis if fx_rate else "", "memo": memo, "source_ref": source,
            "booked_by": booked_by}


def payroll_accrual(ctx: MonthContext, entity: str) -> list[dict]:
    register = ctx.rows(f"payroll_register_{entity}.csv")
    if not register:
        return []
    entry, source = f"JE-{ctx.month}-{entity}-PAYROLL", ctx.path(f"payroll_register_{entity}.csv")
    day = dates.last_business_day(ctx.month).isoformat()
    expense: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    liability: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in register:
        expense[("6000", row["department"])] += Decimal(row["gross_pay"])
        liability["2100"] += Decimal(row["net_pay"])
        for component in EMPLOYEE_COMPONENTS[entity] + EMPLOYER_COMPONENTS[entity]:
            kind, amount = COMPONENT_KIND[component], Decimal(row[component])
            liability[LIABILITY_FOR_KIND[kind]] += amount
            if kind in EXPENSE_FOR_KIND:
                expense[(EXPENSE_FOR_KIND[kind], row["department"])] += amount
    memo = f"Payroll {ctx.month} {entity}"
    lines = [_line(entry, entity, ctx.month, day, a, v, department=d, memo=memo, source=f"{source}#department={d}",
                   booked_by=BOOKED_BY["payroll"]) for (a, d), v in sorted(expense.items()) if v]
    lines += [_line(entry, entity, ctx.month, day, a, -v, memo=memo, source=source, booked_by=BOOKED_BY["payroll"])
              for a, v in sorted(liability.items()) if v]
    return lines


def cash_entries(ctx: MonthContext, entity: str) -> list[dict]:
    lines, by_day = [], defaultdict(list)
    for bank in ctx.rows(f"bank_statement_{entity}.csv"):
        by_day[bank["date"]].append(bank)
    for day in sorted(by_day):
        salaries = [b for b in by_day[day] if b["reference"].startswith("PAY-")]
        if salaries and is_closed(ctx.month):
            entry = f"JE-{ctx.month}-{entity}-NETPAY-{day}"
            total = -sum((Decimal(b["amount"]) for b in salaries), ZERO)
            lines.append(_line(entry, entity, ctx.month, day, "2100", total, memo=f"Net pay cleared {day}",
                               source=ctx.path(f"bank_statement_{entity}.csv"), booked_by=BOOKED_BY["cash"]))
            lines += [_line(entry, entity, ctx.month, day, "1000", Decimal(b["amount"]), memo=b["description"],
                            source=b["bank_line_id"], booked_by=BOOKED_BY["cash"]) for b in salaries]
        for bank in (b for b in by_day[day] if b["reference"].startswith("REM-")):
            _, paid_for_y, paid_for_m, _, code = bank["reference"].split("-")
            split = next(s for _, c, s in remittance_split(entity, ctx.state.previous_register.get(entity, []))
                         if c == code)
            entry = f"JE-{ctx.month}-{entity}-REMIT-{code}"
            paid_for = f"{paid_for_y}-{paid_for_m}"
            lines += [_line(entry, entity, ctx.month, day, a, v, memo=bank["description"],
                            source=(f"months/{paid_for}/payroll_register_{entity}.csv" if paid_for >= C.FIRST_FILE_MONTH
                                    else f"{OPENING_FILE}#entity={entity};account={a}"),
                            booked_by=BOOKED_BY["cash"]) for a, v in split.items()]
            lines.append(_line(entry, entity, ctx.month, day, "1000", Decimal(bank["amount"]), memo=bank["description"],
                               source=bank["bank_line_id"], booked_by=BOOKED_BY["cash"]))
    return lines


def stock_entry(ctx: MonthContext, entity: str) -> list[dict]:
    rows = [r for r in ctx.rows("equity_report.csv") if r["entity"] == entity]
    by_department: dict[str, Decimal] = defaultdict(lambda: ZERO)
    rate = fx.month_average(C.ENTITIES[entity]["currency"], ctx.month)
    for row in rows:
        by_department[row["department"]] += fx.to_local(Decimal(row["expense_month_usd"]), rate)
    if not any(by_department.values()):
        return []
    entry, day = f"JE-{ctx.month}-{entity}-STOCKCOMP", dates.month_end(ctx.month).isoformat()
    memo, source = f"Stock compensation {ctx.month} {entity} (ASC 718)", ctx.path("equity_report.csv")
    lines = [_line(entry, entity, ctx.month, day, "6300", v, department=d, memo=memo, source=f"{source}#department={d}",
                   booked_by=BOOKED_BY["stock"], fx_rate=str(rate.rate), fx_basis=rate.citation)
             for d, v in sorted(by_department.items()) if v]
    total = sum(by_department.values(), ZERO)
    lines.append(_line(entry, entity, ctx.month, day, "3100", -total, memo=memo, source=source,
                       booked_by=BOOKED_BY["stock"], fx_rate=str(rate.rate), fx_basis=rate.citation))
    return lines


def opening_balances(world: World, previous_register: dict[str, list[dict]]) -> list[dict]:
    """The previous ledger's closing balances on the day before the first file month."""
    cutover = dates.add_months(C.FIRST_FILE_MONTH, -1)
    as_of = dates.month_end(cutover)
    apic: dict[str, Decimal] = defaultdict(lambda: ZERO)
    rates = {e: fx.month_average(v["currency"], cutover) for e, v in C.ENTITIES.items()}
    for grant in world.grants:
        person = world.person(grant.employee_id)
        if person.hire_date > as_of:
            continue
        entity = person.stint_on(person.exit_date if person.exit_date and person.exit_date <= as_of else as_of).entity
        terms = stock.GrantTerms(grant.shares, grant.fair_value_per_share, grant.grant_date, grant.vest_start,
                                 grant.vest_months, grant.cliff_months)
        apic[entity] += fx.to_local(stock.cumulative_expense(terms, person.exit_date, as_of)[3], rates[entity])
    rows = []
    for entity in C.ENTITIES:
        register = previous_register.get(entity, [])
        owed = defaultdict(lambda: ZERO)
        for _, _, split in remittance_split(entity, register):
            for account, value in split.items():
                owed[account] += value
        gross = sum((Decimal(r["gross_pay"]) for r in register), ZERO)
        cash = (3 * gross + sum(owed.values(), ZERO)).quantize(Decimal("1000"))
        balances = {"1000": cash, **{a: -v for a, v in sorted(owed.items())}, "3100": -apic[entity]}
        balances["3200"] = -sum(balances.values(), ZERO)
        basis = {"1000": "bank balance at cutover (synthetic: three months of gross payroll plus amounts owed)",
                 "3100": f"cumulative stock compensation to {as_of} at {rates[entity].citation} (policy FIN-02)",
                 "3200": "balancing figure: retained earnings at cutover"}
        for account, value in balances.items():
            rows.append({"entity": entity, "as_of": as_of.isoformat(), "account": account,
                         "account_name": ACCOUNT_NAMES[account],
                         "debit": fmt(value) if value > 0 else "0.00", "credit": fmt(-value) if value < 0 else "0.00",
                         "currency": C.ENTITIES[entity]["currency"],
                         "fx_rate": str(rates[entity].rate) if account == "3100" else "",
                         "basis": basis.get(account, f"{cutover} payroll withholdings and contributions not yet remitted")})
    return rows


def opening_entry(ctx: MonthContext, entity: str) -> list[dict]:
    entry, day = f"JE-{ctx.month}-{entity}-OPENING", dates.month_start(ctx.month).isoformat()
    memo = f"Opening balances at {dates.month_end(dates.add_months(ctx.month, -1))} from the previous ledger"
    return [_line(entry, entity, ctx.month, day, r["account"], Decimal(r["debit"]) - Decimal(r["credit"]), memo=memo,
                  source=f"{OPENING_FILE}#entity={entity};account={r['account']}", booked_by=BOOKED_BY["opening"],
                  fx_rate=r["fx_rate"], fx_basis=r["basis"])
            for r in ctx.bundle.files[OPENING_FILE] if r["entity"] == entity and (r["debit"] != "0.00" or r["credit"] != "0.00")]


def build_ledger(ctx: MonthContext) -> list[dict]:
    lines = []
    for entity in C.ENTITIES:
        if ctx.month == C.FIRST_FILE_MONTH:
            lines += opening_entry(ctx, entity)
        if is_closed(ctx.month):
            lines += payroll_accrual(ctx, entity)
        lines += cash_entries(ctx, entity)
        if is_closed(ctx.month):
            lines += stock_entry(ctx, entity)
    return lines


# -- pipeline ----------------------------------------------------------------
def payroll_master(world: World) -> dict[tuple[str, str], str]:
    master = {}
    for person in world.people:
        for stint in person.stints:
            master[(person.employee_id, stint.entity)] = stint.bank_account
    return master


def run(world: World, bundle: Bundle, hooks: dict[tuple[str, str], list[Hook]] | None = None) -> None:
    hooks = hooks or {}
    state, master = PipelineState(), payroll_master(world)

    def stage(ctx: MonthContext, name: str) -> None:
        for hook in hooks.get((ctx.month, name), []):
            hook(ctx)

    first = min(payroll_start(e) for e in C.ENTITIES)
    for month in dates.month_range(first, C.OPEN_MONTH):
        written = month >= C.FIRST_FILE_MONTH
        if month == C.FIRST_FILE_MONTH:
            bundle.files[OPENING_FILE] = opening_balances(world, state.previous_register)
        ctx = MonthContext(month, world, bundle if written else Bundle(), master, state)
        if written:
            bundle.files[ctx.path("hr_census.csv")] = hr_census(world, month)
            stage(ctx, "hr")
        for entity in C.ENTITIES:
            if month >= payroll_start(entity):
                ctx.bundle.files[ctx.path(f"payroll_register_{entity}.csv")] = build_register(ctx, entity)
        stage(ctx, "register")
        if written:
            for entity in C.ENTITIES:
                bundle.files[ctx.path(f"payment_file_{entity}.csv")] = build_payments(ctx, entity)
            stage(ctx, "payments")
            bundle.files[ctx.path("equity_report.csv")] = build_equity(ctx)
            stage(ctx, "equity")
            for entity in C.ENTITIES:
                bundle.files[ctx.path(f"bank_statement_{entity}.csv")] = build_bank(ctx, entity)
            stage(ctx, "bank")
            bundle.files[ctx.path("ledger_booked.csv")] = build_ledger(ctx)
            stage(ctx, "ledger")
        for entity in C.ENTITIES:
            register = ctx.rows(f"payroll_register_{entity}.csv")
            for row in register:
                ty = int(row["tax_year"])
                state.ytd[(entity, row["employee_id"], ty)] = {
                    k: Decimal(row[f"ytd_{k}"]) for k in YTD_KEYS[entity]}
            state.previous_register[entity] = register
