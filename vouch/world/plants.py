"""Planted mistakes, the main set. Each plant is a small function that introduces one
mistake into one source in one month (later stages then carry it the way a real
company's would) and records the answer-key entry for it.

Subjects are drawn at random, by criteria only, from people who fit. No plant names
a specific employee, grant or payment."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

from vouch import dates
from vouch.money import ZERO, cents, fmt
from vouch.world import config as C
from vouch.world.banking import new_account
from vouch.world.payroll import MonthContext, register_row
from vouch.world.simulate import Casting, Person, band_salary

OPEN = C.OPEN_MONTH
ROLE_NAME = {d["role"]: d["name"] for d in C.DIRECTORY}


def month(offset: int) -> str:
    return dates.add_months(OPEN, offset)


@dataclass
class Plant:
    plant_id: str
    kind: str
    month: str
    hooks: list[tuple[str, str, Callable[[MonthContext], None]]] = field(default_factory=list)
    key: dict = field(default_factory=dict)

    def on(self, month_key: str, stage: str):
        def register(fn):
            self.hooks.append((month_key, stage, fn))
            return fn
        return register


class Planter:
    """Shared state for one build: seeded randomness per plant and subjects already used."""

    def __init__(self, seed: int):
        self.seed = seed
        self.claimed: set[str] = set()

    def rng(self, plant_id: str) -> random.Random:
        return random.Random(f"{self.seed}:{plant_id}")

    def claim(self, employee_id: str) -> None:
        self.claimed.add(employee_id)


def hooks_for(plants: list[Plant]) -> dict[tuple[str, str], list[Callable]]:
    out: dict[tuple[str, str], list[Callable]] = {}
    for plant in plants:
        for month_key, stage, fn in plant.hooks:
            out.setdefault((month_key, stage), []).append(fn)
    return out


# -- helpers -----------------------------------------------------------------
def steady(ctx: MonthContext, planter: Planter, months_back: int = 0, entity: str | None = None) -> list[tuple[Person, str]]:
    """People in one entity for the whole month and the `months_back` months before it, not used by another plant."""
    first, last = dates.month_start(dates.add_months(ctx.month, -months_back)), dates.month_end(ctx.month)
    out = []
    for person in ctx.world.people:
        if person.employee_id in planter.claimed or person.hire_date >= first:
            continue
        if person.exit_date and person.exit_date <= last:
            continue
        stint = person.stint_on(first)
        if stint.start >= first or (stint.end and stint.end < last) or (entity and stint.entity != entity):
            continue
        out.append((person, stint.entity))
    return out


def pick(rng: random.Random, planter: Planter, candidates: list):
    if not candidates:
        raise RuntimeError("no eligible subject for plant")
    chosen = rng.choice(candidates)
    planter.claim(chosen[0].employee_id if isinstance(chosen, tuple) else chosen.employee_id)
    return chosen


def row_of(rows: list[dict], **match) -> dict:
    return next(r for r in rows if all(r[k] == v for k, v in match.items()))


def add_email(ctx: MonthContext, received_at: str, sender: str, subject: str, body: str, to: str = "payroll") -> str:
    emails = ctx.bundle.files["standing/inbox.json"]["emails"]
    email_id = f"EM-{max(int(e['email_id'][3:]) for e in emails) + 1:05d}"
    emails.append({"email_id": email_id, "received_at": received_at, "from": sender, "to": f"{to}@{C.DOMAIN}",
                   "subject": subject, "body": body})
    return email_id


def next_payment_id(rows: list[dict], month_key: str, entity: str) -> str:
    return f"PAY-{month_key}-{entity}-{max(int(r['payment_id'].rsplit('-', 1)[1]) for r in rows) + 1:04d}"


def renumber_bank(rows: list[dict], entity: str, month_key: str) -> None:
    for n, row in enumerate(rows, 1):
        row["bank_line_id"] = f"BNK-{entity}-{month_key}-{n:04d}"


def amount(value: Decimal, currency: str, basis: str, verification: str) -> dict:
    return {"value": fmt(value), "currency": currency, "basis": basis, "verification": verification}


def key(plant: Plant, **fields) -> None:
    plant.key.update({"plant_id": plant.plant_id, "kind": plant.kind, "month": plant.month, **fields})


def currency(entity: str) -> str:
    return C.ENTITIES[entity]["currency"]


# -- the main set ------------------------------------------------------------
def bank_change_by_email(plant_id: str, planter: Planter) -> Plant:
    """An email asks to redirect pay days before pay day; the payment file was changed to match."""
    plant = Plant(plant_id, "bank_change_by_email", OPEN)

    @plant.on(OPEN, "payments")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        person, entity = pick(rng, planter, steady(ctx, planter, months_back=2))
        payments = ctx.rows(f"payment_file_{entity}.csv")
        payment = row_of(payments, employee_id=person.employee_id)
        old, new = payment["bank_account"], new_account(entity, rng, ctx.world.bank_accounts_taken)
        payment["bank_account"] = new
        sent = dates.last_business_day(OPEN) - timedelta(days=5)
        email_id = add_email(
            ctx, f"{sent}T06:58:00", f"{person.first_name.lower()}.{person.last_name.lower()}@secure-payroll-mail.com",
            "Urgent - new bank account for this month's salary",
            f"Hi payroll team, this is {person.name}. My old account is being closed so please send this month's "
            f"salary to my new account {new}. I'm travelling and can't take calls, email is best. Thank you!")
        key(plant, subject={"employee_id": person.employee_id, "payment_id": payment["payment_id"], "entity": entity,
                            "email_id": email_id, "hr_bank_account": old, "requested_bank_account": new},
            sources_disagree=["standing/inbox.json", ctx.path(f"payment_file_{entity}.csv"), ctx.path("hr_census.csv")],
            trusted_source="hr_census", expected_outcome="hold_payment",
            expected_behaviour="Hold the payment, cite the earlier ruling, ask HR for a call-back to the number on file.",
            ask={"role": "hr", "person": ROLE_NAME["hr"]}, rules=["PAY-06", "FIN-09"], precedents=["RUL-0001"],
            expected_amount=amount(Decimal(payment["amount"]), currency(entity), "payment held",
                                   "copied-from-source"))
    return plant


def leaver_still_paid(plant_id: str, planter: Planter) -> Plant:
    """Payroll was never told about an exit and paid the leaver for the next full month."""
    paid_month = month(-1)
    plant = Plant(plant_id, "leaver_still_paid", paid_month)

    @plant.on(paid_month, "register")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        exit_month = month(-2)
        leavers = [p for p in ctx.world.people if p.exit_date and dates.month_of(p.exit_date) == exit_month
                   and len(p.stints) == 1 and p.employee_id not in planter.claimed]
        person = pick(rng, planter, leavers)
        entity = person.stints[0].entity
        salary = person.salary_on(person.exit_date)
        row = register_row(entity, paid_month, person.employee_id, person.name, person.department,
                           person.level_on(person.exit_date), salary.annual_salary,
                           dates.days_in_month(paid_month), ctx.ytd_before(entity, person.employee_id))
        register = ctx.rows(f"payroll_register_{entity}.csv")
        register.append(row)            # manual additions come at the end of the provider's run
        gross = (salary.annual_salary / 12).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        key(plant, subject={"employee_id": person.employee_id, "entity": entity, "exit_date": person.exit_date.isoformat(),
                            "paid_period": paid_month},
            sources_disagree=[ctx.path(f"payroll_register_{entity}.csv"), ctx.path("hr_census.csv")],
            trusted_source="hr_census", expected_outcome="draft_recovery_in_open_month",
            expected_behaviour="Draft the recovery of the overpayment in the open month.",
            ask={"role": "hr", "person": ROLE_NAME["hr"]}, rules=["PAY-05", "FIN-05", "FIN-09"], precedents=[],
            expected_amount=amount(gross, currency(entity), "gross overpaid = annual salary / 12",
                                   "independent-arithmetic"),
            net_paid=amount(Decimal(row["net_pay"]), currency(entity), "net pay that left the bank",
                            "engine-output-pending-hand-check"))
    return plant


def leaver_paid_years_later(plant_id: str, planter: Planter) -> Plant:
    """Someone who left years ago reappears on this month's register."""
    plant = Plant(plant_id, "leaver_paid_years_later", OPEN)

    @plant.on(OPEN, "register")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        cutoff = dates.month_start(month(-24))
        leavers = [p for p in ctx.world.people if p.exit_date and p.exit_date < cutoff
                   and p.employee_id not in planter.claimed]
        person = pick(rng, planter, leavers)
        entity = person.stints[-1].entity
        salary = person.salary_on(person.exit_date).annual_salary
        row = register_row(entity, OPEN, person.employee_id, person.name, person.department,
                           person.level_on(person.exit_date), salary, dates.days_in_month(OPEN), {})
        register = ctx.rows(f"payroll_register_{entity}.csv")
        register.append(row)            # manual additions come at the end of the provider's run
        key(plant, subject={"employee_id": person.employee_id, "entity": entity, "exit_date": person.exit_date.isoformat()},
            sources_disagree=[ctx.path(f"payroll_register_{entity}.csv"), ctx.path("hr_census.csv")],
            trusted_source="hr_census", expected_outcome="hold_payment_and_recover",
            expected_behaviour="Hold the payment and recover anything paid.",
            ask={"role": "hr", "person": ROLE_NAME["hr"]}, rules=["PAY-05", "FIN-09"], precedents=[],
            expected_amount=amount(Decimal(row["net_pay"]), currency(entity), "net payment to hold",
                                   "engine-output-pending-hand-check"),
            gross=amount((salary / 12).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), currency(entity),
                         "gross = annual salary at exit / 12", "independent-arithmetic"))
    return plant


def forfeited_grant_still_expensed(plant_id: str, planter: Planter) -> Plant:
    """The equity platform never recorded an exit before the cliff and kept expensing the grant."""
    exit_month = month(-3)
    plant = Plant(plant_id, "forfeited_grant_still_expensed", exit_month)
    chosen: dict = {}

    def platform_row(ctx: MonthContext) -> dict:
        return row_of(ctx.rows("equity_report.csv"), grant_id=chosen["grant"].grant_id)

    @plant.on(exit_month, "equity")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        candidates = []
        for grant in ctx.world.grants:
            person = ctx.world.person(grant.employee_id)
            if (person.exit_date and dates.month_of(person.exit_date) == exit_month and grant.grant_date
                    and grant.grant_date < person.exit_date and person.employee_id not in planter.claimed):
                row = row_of(ctx.rows("equity_report.csv"), grant_id=grant.grant_id)
                if row["status"] == "forfeited" and row["vested_shares"] == "0":
                    candidates.append(grant)
        if not candidates:
            raise RuntimeError("no eligible subject for plant")
        chosen["grant"] = rng.choice(candidates)
        planter.claim(chosen["grant"].employee_id)

    def straight_line(grant, month_key: str) -> Decimal:
        """Cumulative USD expense at month end for a holder still employed (plain arithmetic)."""
        end = dates.month_end(month_key)
        months = (end.year - grant.vest_start.year) * 12 + end.month - grant.vest_start.month
        total = cents(Decimal(grant.shares) * grant.fair_value_per_share)
        return cents(total * min(months, grant.vest_months) / grant.vest_months), months

    def keep_expensing(ctx: MonthContext) -> None:
        grant, row = chosen["grant"], platform_row(ctx)
        cumulative, months = straight_line(grant, ctx.month)
        expense = cumulative - straight_line(grant, dates.add_months(ctx.month, -1))[0]
        row.update({"status": "active", "service_months": str(months),
                    "vested_shares": str(grant.shares * min(months, 48) // 48 if months >= grant.cliff_months else 0),
                    "expense_month_usd": fmt(expense), "cumulative_expense_usd": fmt(cumulative),
                    "expense_month_local": fmt(cents(expense * Decimal(row["fx_rate"])))})
        chosen.setdefault("months", {})[ctx.month] = {"expense_usd": fmt(expense), "cumulative_usd": fmt(cumulative)}

    for month_key in dates.month_range(exit_month, OPEN):
        plant.on(month_key, "equity")(keep_expensing)

    @plant.on(OPEN, "ledger")
    def _(ctx: MonthContext) -> None:
        grant, person = chosen["grant"], ctx.world.person(chosen["grant"].employee_id)
        row = platform_row(ctx)
        booked_cum = Decimal(chosen["months"][month(-1)]["cumulative_usd"])
        key(plant, subject={"grant_id": grant.grant_id, "employee_id": grant.employee_id, "entity": row["entity"],
                            "exit_date": person.exit_date.isoformat(), "platform_months": chosen["months"]},
            sources_disagree=[f"months/{k}/equity_report.csv" for k in dates.month_range(exit_month, OPEN)]
            + [f"months/{exit_month}/hr_census.csv"],
            trusted_source="hr_census", expected_outcome="reverse_expense",
            expected_behaviour="Reverse the stock expense recognised for the forfeited grant and book nothing more.",
            ask={"role": "stock_admin", "person": ROLE_NAME["stock_admin"]}, rules=["FIN-07", "FIN-05", "FIN-09"],
            precedents=[],
            expected_amount=amount(booked_cum, "USD",
                                   "cumulative expense booked to the end of the last closed month; correct "
                                   "cumulative is zero because the holder left before the cliff",
                                   "independent-arithmetic"),
            open_month_expense_to_block=amount(Decimal(row["expense_month_usd"]), "USD",
                                               "platform expense for the open month", "copied-from-source"))
    return plant


def grant_without_board_approval(plant_id: str, planter: Planter) -> Plant:
    """The platform shows a grant as live and expensed although the board never approved it."""
    plant = Plant(plant_id, "grant_without_board_approval", OPEN)

    @plant.on(OPEN, "equity")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        rows = [r for r in ctx.rows("equity_report.csv") if r["status"] == "pending_approval"
                and int(r["service_months"]) >= 1 and r["employee_id"] not in planter.claimed]
        if not rows:
            raise RuntimeError("no eligible subject for plant")
        row = rng.choice(rows)
        planter.claim(row["employee_id"])
        fair_value = Decimal(rng.randint(550, 700)) / 100
        total = cents(Decimal(row["shares"]) * fair_value)
        expense = cents(total * int(row["service_months"]) / 48)
        approved_on = dates.month_start(OPEN) + timedelta(days=9)
        row.update({"status": "active", "grant_date": approved_on.isoformat(),
                    "fair_value_per_share_usd": f"{fair_value:.2f}", "board_resolution": "",
                    "expense_month_usd": fmt(expense), "cumulative_expense_usd": fmt(expense),
                    "expense_month_local": fmt(cents(expense * Decimal(row["fx_rate"])))})
        key(plant, subject={"grant_id": row["grant_id"], "employee_id": row["employee_id"], "entity": row["entity"]},
            sources_disagree=[ctx.path("equity_report.csv"), "standing/board_approvals.csv", "standing/signed_grants.csv"],
            trusted_source="board_approvals", expected_outcome="block_expense",
            expected_behaviour="Block the expense and ask the stock admin for the board approval.",
            ask={"role": "stock_admin", "person": ROLE_NAME["stock_admin"]}, rules=["FIN-07", "FIN-09"], precedents=[],
            expected_amount=amount(expense, "USD", "shares x platform fair value x service months / 48",
                                   "independent-arithmetic"))
    return plant


def wrong_pension_rate(plant_id: str, planter: Planter) -> Plant:
    """The payroll provider ran one month's UK payroll at a 3% employer pension instead of the policy's 5%."""
    run_month = month(-4)
    plant = Plant(plant_id, "wrong_employer_pension_rate", run_month)
    wrong = Decimal("0.03")

    @plant.on(run_month, "register")
    def _(ctx: MonthContext) -> None:
        register = ctx.rows("payroll_register_UK.csv")
        shortfall = ZERO
        for i, row in enumerate(register):
            ytd = {"gross": Decimal(row["ytd_gross"]) - Decimal(row["gross_pay"])}
            planted = register_row("UK", run_month, row["employee_id"], row["employee_name"], row["department"],
                                   int(row["level"]), Decimal(row["annual_salary"]), int(row["days_paid"]), ytd,
                                   employer_pension_rate=wrong)
            register[i] = planted
            gross = Decimal(row["gross_pay"])
            shortfall += (gross * Decimal("0.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) \
                - (gross * wrong).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        key(plant, subject={"entity": "UK", "period": run_month, "rows": len(register), "rate_used": str(wrong),
                            "policy_rate": "0.05"},
            sources_disagree=[ctx.path("payroll_register_UK.csv"), "standing/pay_policy.json"],
            trusted_source="pay_policy", expected_outcome="recompute_and_draft_correction",
            expected_behaviour="Recompute the employer pension at the policy rate and draft the correction in the open month.",
            ask={"role": "finance_manager", "person": ROLE_NAME["finance_manager"]}, rules=["PAY-04", "FIN-05", "FIN-09"],
            precedents=[],
            expected_amount=amount(shortfall, "GBP", "sum over rows of round(gross x 5%) - round(gross x 3%)",
                                   "independent-arithmetic"))
    return plant


def ghost_on_payroll(plant_id: str, planter: Planter) -> Plant:
    """A person who is not in HR appears on the register with a payment."""
    plant = Plant(plant_id, "person_not_in_hr", OPEN)
    ghost: dict = {}

    @plant.on(OPEN, "register")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        entity = rng.choice(list(C.ENTITIES))
        department = rng.choice(C.DEPARTMENTS)
        highest = max(int(p.employee_id[3:]) for p in ctx.world.people)
        employee_id = f"NL-{highest + rng.randint(40, 90):05d}"
        name = f"{rng.choice(C.FIRST_NAMES[entity])} {rng.choice(C.LAST_NAMES[entity])}"
        salary = band_salary(entity, department, 3, rng)
        row = register_row(entity, OPEN, employee_id, name, department, 3, salary, dates.days_in_month(OPEN), {})
        register = ctx.rows(f"payroll_register_{entity}.csv")
        register.append(row)            # manual additions come at the end of the provider's run
        ctx.master[(employee_id, entity)] = new_account(entity, rng, ctx.world.bank_accounts_taken)
        ghost.update(employee_id=employee_id, entity=entity, net=row["net_pay"])

    @plant.on(OPEN, "payments")
    def _(ctx: MonthContext) -> None:
        payment = row_of(ctx.rows(f"payment_file_{ghost['entity']}.csv"), employee_id=ghost["employee_id"])
        key(plant, subject={"employee_id": ghost["employee_id"], "entity": ghost["entity"],
                            "payment_id": payment["payment_id"]},
            sources_disagree=[ctx.path(f"payroll_register_{ghost['entity']}.csv"), ctx.path("hr_census.csv")],
            trusted_source="hr_census", expected_outcome="hold_send_no_money",
            expected_behaviour="Hold the payment and send no money until HR confirms the person exists.",
            ask={"role": "hr", "person": ROLE_NAME["hr"]}, rules=["FIN-09", "PAY-03"], precedents=[],
            expected_amount=amount(Decimal(payment["amount"]), currency(ghost["entity"]), "payment held",
                                   "copied-from-source"))
    return plant


def backdated_raise(plant_id: str, planter: Planter) -> Plant:
    """A raise approved this month is effective from a month that is already closed."""
    plant = Plant(plant_id, "backdated_raise_into_closed_month", OPEN)
    chosen: dict = {}

    @plant.on(OPEN, "hr")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        person, entity = pick(rng, planter, steady(ctx, planter, months_back=3))
        old = person.salary_on(dates.month_start(OPEN)).annual_salary
        new = (old * Decimal("1.08") / 500).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 500
        effective = dates.month_start(month(-2))
        changes = ctx.bundle.files["standing/salary_changes.csv"]
        change_id = f"SC-{max(int(c['change_id'][3:]) for c in changes) + 1:05d}"
        changes.append({"change_id": change_id, "employee_id": person.employee_id, "effective_date": effective.isoformat(),
                        "annual_salary": fmt(new), "currency": currency(entity), "previous_salary": fmt(old),
                        "reason": "off_cycle_adjustment", "level": str(person.level_on(effective)),
                        "approved_by": next(d["name"] for d in C.DIRECTORY if d.get("department") == person.department),
                        "approved_on": (dates.month_start(OPEN) + timedelta(days=7)).isoformat()})
        chosen.update(person=person, entity=entity, old=old, new=new, change_id=change_id, effective=effective)

    @plant.on(OPEN, "register")
    def _(ctx: MonthContext) -> None:
        person, entity = chosen["person"], chosen["entity"]
        register = ctx.rows(f"payroll_register_{entity}.csv")
        i = register.index(row_of(register, employee_id=person.employee_id))
        register[i] = register_row(entity, OPEN, person.employee_id, person.name, person.department,
                                   person.level_on(dates.month_start(OPEN)), chosen["new"], dates.days_in_month(OPEN),
                                   ctx.ytd_before(entity, person.employee_id))
        closed = dates.month_range(month(-2), month(-1))
        arrears = sum(((chosen["new"] - chosen["old"]) / 12).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                      for _ in closed)
        key(plant, subject={"employee_id": person.employee_id, "entity": entity, "change_id": chosen["change_id"],
                            "effective_date": chosen["effective"].isoformat(), "closed_months_affected": closed,
                            "old_salary": fmt(chosen["old"]), "new_salary": fmt(chosen["new"])},
            sources_disagree=["standing/salary_changes.csv", f"months/{closed[0]}/payroll_register_{entity}.csv",
                              f"months/{closed[-1]}/payroll_register_{entity}.csv"],
            trusted_source="salary_changes", expected_outcome="hold_then_pay_arrears_in_open_month_with_approval",
            expected_behaviour="Hold; pay the difference for the closed months only in the open month, with approval.",
            ask={"role": "controller", "person": ROLE_NAME["controller"]}, rules=["PAY-03", "FIN-05"],
            precedents=["RUL-0003"],
            expected_amount=amount(arrears, currency(entity), "gross arrears = (new - old) / 12 per closed month",
                                   "independent-arithmetic"))
    return plant


def half_cleared_batch(plant_id: str, planter: Planter) -> Plant:
    """Only every other line of one entity's net pay batch cleared the bank."""
    run_month = month(-1)
    plant = Plant(plant_id, "payment_batch_half_cleared", run_month)

    @plant.on(run_month, "bank")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        entity = rng.choice(["UK", "DE"])
        bank = ctx.rows(f"bank_statement_{entity}.csv")
        salaries = [b for b in bank if b["reference"].startswith("PAY-")]
        payees = {p["payment_id"]: p["employee_id"] for p in ctx.rows(f"payment_file_{entity}.csv")}
        eligible = [b for b in salaries if payees[b["reference"]] not in planter.claimed]
        failed = eligible[1::2]
        for line in failed:
            bank.remove(line)
        renumber_bank(bank, entity, run_month)
        unpaid = sum((-Decimal(b["amount"]) for b in failed), ZERO)
        key(plant, subject={"entity": entity, "batch_id": f"BATCH-{run_month}-{entity}",
                            "lines_in_batch": len(salaries), "lines_eligible": len(eligible),
                            "unpaid_payment_ids": [b["reference"] for b in failed]},
            sources_disagree=[ctx.path(f"bank_statement_{entity}.csv"), ctx.path(f"payment_file_{entity}.csv")],
            trusted_source="bank_statement", expected_outcome="request_only_unpaid_lines",
            expected_behaviour="Request funding and release for the unpaid lines only.",
            ask={"role": "treasury", "person": ROLE_NAME["treasury"]}, rules=["FIN-06", "FIN-08"], precedents=[],
            expected_amount=amount(unpaid, currency(entity), "sum of payment lines missing from the bank",
                                   "independent-arithmetic"))
    return plant


def booked_but_never_cleared(plant_id: str, planter: Planter) -> Plant:
    """Treasury booked a salary payment from the payment file; the bank never cleared it."""
    run_month = month(-2)
    plant = Plant(plant_id, "booked_payment_never_cleared", run_month)
    chosen: dict = {}

    @plant.on(run_month, "bank")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        bank = ctx.rows("bank_statement_US.csv")
        payees = {p["payment_id"]: p for p in ctx.rows("payment_file_US.csv")}
        lines = [b for b in bank if b["reference"].startswith("PAY-")
                 and payees[b["reference"]]["employee_id"] not in planter.claimed]
        line = rng.choice(lines)
        planter.claim(payees[line["reference"]]["employee_id"])
        bank.remove(line)
        renumber_bank(bank, "US", run_month)
        chosen.update(payment=payees[line["reference"]])

    @plant.on(run_month, "ledger")
    def _(ctx: MonthContext) -> None:
        payment, ledger = chosen["payment"], ctx.rows("ledger_booked.csv")
        entry = f"JE-{run_month}-US-NETPAY-MANUAL"
        base = {"entry_id": entry, "entity": "US", "period": run_month, "date": payment["value_date"],
                "currency": "USD", "fx_rate": "", "department": "", "booked_by": ROLE_NAME["treasury"],
                "memo": f"SALARY {payment['payee_name'].upper()} booked from payment file",
                "source_ref": payment["payment_id"]}
        ledger.append({**base, "account": "2100", "account_name": "Net pay payable", "debit": payment["amount"], "credit": "0.00"})
        ledger.append({**base, "account": "1000", "account_name": "Cash at bank", "debit": "0.00", "credit": payment["amount"]})
        key(plant, subject={"entity": "US", "payment_id": payment["payment_id"], "employee_id": payment["employee_id"],
                            "entry_id": entry},
            sources_disagree=[ctx.path("ledger_booked.csv"), ctx.path("bank_statement_US.csv")],
            trusted_source="bank_statement", expected_outcome="trust_bank_keep_reconciling_item_open",
            expected_behaviour="Trust the bank and keep a reconciling item open until the payment clears.",
            ask={"role": "treasury", "person": ROLE_NAME["treasury"]}, rules=["FIN-06"], precedents=[],
            expected_amount=amount(Decimal(payment["amount"]), "USD", "booked payment absent from the bank",
                                   "copied-from-source"))
    return plant


def duplicate_payment(plant_id: str, planter: Planter) -> Plant:
    """The same salary payment appears twice in the payment file under two payment ids."""
    plant = Plant(plant_id, "duplicate_payment", OPEN)

    @plant.on(OPEN, "payments")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        person, entity = pick(rng, planter, steady(ctx, planter, months_back=1))
        payments = ctx.rows(f"payment_file_{entity}.csv")
        original = row_of(payments, employee_id=person.employee_id)
        duplicate = {**original, "payment_id": next_payment_id(payments, OPEN, entity)}
        payments.append(duplicate)
        key(plant, subject={"employee_id": person.employee_id, "entity": entity, "original_payment_id": original["payment_id"],
                            "duplicate_payment_id": duplicate["payment_id"]},
            sources_disagree=[ctx.path(f"payment_file_{entity}.csv")],
            trusted_source="payment_file", expected_outcome="reject_duplicate",
            expected_behaviour="Reject the duplicate payment and keep the original.",
            ask={"role": "treasury", "person": ROLE_NAME["treasury"]}, rules=["FIN-08"], precedents=[],
            expected_amount=amount(Decimal(duplicate["amount"]), currency(entity), "duplicate payment rejected",
                                   "copied-from-source"))
    return plant


def transfer_not_reflected(plant_id: str, planter: Planter) -> Plant:
    """HR records a move to another entity from the 1st; payroll and the platform still use the old one."""
    plant = Plant(plant_id, "entity_transfer_not_reflected", OPEN)

    @plant.on(OPEN, "hr")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        person, entity = pick(rng, planter, steady(ctx, planter, months_back=2))
        new_entity = rng.choice([e for e in C.ENTITIES if e != entity])
        census = row_of(ctx.rows("hr_census.csv"), employee_id=person.employee_id)
        census.update({"entity": new_entity, "entity_since": dates.month_start(OPEN).isoformat(),
                       "bank_account": new_account(new_entity, rng, ctx.world.bank_accounts_taken)})
        head = next(d["name"] for d in C.DIRECTORY if d.get("department") == person.department)
        key(plant, subject={"employee_id": person.employee_id, "hr_entity": new_entity, "payroll_entity": entity,
                            "department": person.department},
            sources_disagree=[ctx.path("hr_census.csv"), ctx.path(f"payroll_register_{entity}.csv"),
                              ctx.path("equity_report.csv")],
            trusted_source="hr_census", expected_outcome="hold_and_ask_department_head",
            expected_behaviour="Hold the pay and stock expense for this person and ask the department head to confirm the transfer.",
            ask={"role": "department_head", "person": head}, rules=["PAY-07", "FIN-09"], precedents=[],
            expected_amount=None)
    return plant


def self_approved_accrual(plant_id: str, planter: Planter) -> Plant:
    """A department head requested a large accrual and approved it themself."""
    plant = Plant(plant_id, "requester_approved_large_accrual", OPEN)

    @plant.on(OPEN, "hr")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        log = ctx.bundle.files["standing/approvals_log.csv"]
        requester = rng.choice([d for d in C.DIRECTORY if d["role"] == "department_head"])
        entity = rng.choice(list(C.ENTITIES))
        value = Decimal(rng.randint(12, 30) * 10_000)
        request_id = f"AR-{max(int(r['request_id'][3:]) for r in log) + 1:04d}"
        log.append({"request_id": request_id, "requested_on": (dates.month_end(OPEN) - timedelta(days=2)).isoformat(),
                    "kind": "accrual", "entity": entity, "period": OPEN, "amount": fmt(value),
                    "currency": currency(entity), "description": f"{requester['department']} quarterly bonus accrual",
                    "requested_by": requester["name"], "approved_by": requester["name"],
                    "approved_on": (dates.month_end(OPEN) - timedelta(days=2)).isoformat(), "status": "approved"})
        key(plant, subject={"request_id": request_id, "requested_by": requester["name"], "entity": entity},
            sources_disagree=["standing/approvals_log.csv", "standing/finance_policy.json"],
            trusted_source="finance_policy", expected_outcome="refuse_and_route_to_controller",
            expected_behaviour="Refuse the self-approval and route the accrual to the controller.",
            ask={"role": "controller", "person": ROLE_NAME["controller"]}, rules=["FIN-03", "FIN-04"], precedents=[],
            expected_amount=amount(value, currency(entity), "accrual amount routed for approval", "copied-from-source"))
    return plant


def new_hire_with_leavers_account(plant_id: str, planter: Planter) -> Plant:
    """A new hire's bank details in HR are those of someone who left years ago."""
    plant = Plant(plant_id, "new_hire_with_old_leavers_bank_account", OPEN)

    @plant.on(OPEN, "hr")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(plant_id)
        start = dates.month_start(OPEN)
        hires = [p for p in ctx.world.people if p.hire_date >= start and p.employee_id not in planter.claimed]
        person = pick(rng, planter, hires)
        entity = person.stints[0].entity
        history = ctx.bundle.files["standing/legacy_payment_history.csv"]
        left_long_ago = {p.employee_id for p in ctx.world.people
                         if p.exit_date and p.exit_date.year < int(C.FIRST_FILE_MONTH[:4])}
        old = rng.choice([h for h in history if h["entity"] == entity and h["employee_id"] in left_long_ago])
        census = row_of(ctx.rows("hr_census.csv"), employee_id=person.employee_id)
        census["bank_account"] = old["bank_account"]
        ctx.master[(person.employee_id, entity)] = old["bank_account"]
        key(plant, subject={"employee_id": person.employee_id, "entity": entity, "bank_account": old["bank_account"],
                            "previous_holder_employee_id": old["employee_id"], "previous_holder_year": old["year"]},
            sources_disagree=[ctx.path("hr_census.csv"), "standing/legacy_payment_history.csv"],
            trusted_source="hr_census", expected_outcome="hold_pay_destination",
            expected_behaviour="Hold the pay destination and ask HR for a call-back to confirm the account.",
            ask={"role": "hr", "person": ROLE_NAME["hr"]}, rules=["PAY-06", "FIN-09"], precedents=[],
            expected_amount=None)

    @plant.on(OPEN, "payments")
    def _(ctx: MonthContext) -> None:
        subject = plant.key["subject"]
        payment = row_of(ctx.rows(f"payment_file_{subject['entity']}.csv"), employee_id=subject["employee_id"])
        subject["payment_id"] = payment["payment_id"]
        plant.key["expected_amount"] = amount(Decimal(payment["amount"]), currency(subject["entity"]),
                                              "payment held", "copied-from-source")
    return plant


def _last_board_meeting(on_or_before) -> "date":
    key = dates.month_of(on_or_before)
    while True:
        if dates.parse_month(key)[1] in C.BOARD_MEETING_MONTHS:
            meeting = dates.nth_weekday(key, 3, 3)
            if meeting <= on_or_before:
                return meeting
        key = dates.add_months(key, -1)


def castings() -> list[Casting]:
    """The people the main set needs, hired on purpose so every plant finds a subject that fits its
    exact definition on every seed. Plants still choose by their criteria; a random person may also fit."""
    def day(month_key: str, n: int):
        return dates.month_start(month_key) + timedelta(days=n - 1)

    founded = dates.month_start(C.FOUNDED)
    if dates.parse_month(OPEN)[1] in C.BOARD_MEETING_MONTHS:
        raise ValueError("MAIN-05 needs an open month without a board meeting")
    cast = [
        # MAIN-02: one entity only, leaves in the month before the last closed month.
        Casting("MAIN-02 leaver payroll was not told about", day(month(-20), 1), day(month(-2), 14)),
        # MAIN-03: left more than two years before the open month.
        Casting("MAIN-03 leaver who reappears years later", max(founded, day(month(-48), 1)), day(month(-30), 10)),
        # MAIN-04: approved grant, leaves before the 12-month cliff, three months before the open month.
        Casting("MAIN-04 leaver before the cliff", day(month(-10), 1), day(month(-3), 15), grant=True),
        # MAIN-05: joins after the last board meeting, so the grant is still unapproved, with a month of service.
        Casting("MAIN-05 hire awaiting board approval",
                _last_board_meeting(dates.month_end(month(-1))) + timedelta(days=1), grant=True),
        # MAIN-14: a hire in the open month ...
        Casting("MAIN-14 new hire", day(OPEN, 7)),
    ]
    # ... and, in every entity, someone who left before the current files began (legacy payment history).
    cast += [Casting(f"MAIN-14 earlier leaver ({entity})", max(founded, day(month(-56), 1)), day(month(-34), 5),
                     entity=entity) for entity in C.ENTITIES]
    return cast


MAIN_SET = [
    ("MAIN-01", bank_change_by_email),
    ("MAIN-02", leaver_still_paid),
    ("MAIN-03", leaver_paid_years_later),
    ("MAIN-04", forfeited_grant_still_expensed),
    ("MAIN-05", grant_without_board_approval),
    ("MAIN-06", wrong_pension_rate),
    ("MAIN-07", ghost_on_payroll),
    ("MAIN-08", backdated_raise),
    ("MAIN-09", half_cleared_batch),
    ("MAIN-10", booked_but_never_cleared),
    ("MAIN-11", duplicate_payment),
    ("MAIN-12", transfer_not_reflected),
    ("MAIN-13", self_approved_accrual),
    ("MAIN-14", new_hire_with_leavers_account),
]


def main_set(planter: Planter, only: list[str] | None = None) -> list[Plant]:
    return [factory(plant_id, planter) for plant_id, factory in MAIN_SET if only is None or plant_id in only]
