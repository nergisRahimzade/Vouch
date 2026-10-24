"""Turns the simulated truth into the documents the finance team receives: standing
documents (policies, approvals, grants, inbox, rulings, payment history) and each
month's HR census. Monthly money files are added by vouch.world.payroll."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from vouch import dates
from vouch.money import fmt
from vouch.world import config as C
from vouch.world.policies import FINANCE_POLICY, PAY_POLICY
from vouch.world.simulate import Person, World

FIELDS = {
    "hr_census.csv": ["employee_id", "first_name", "last_name", "email", "entity", "entity_since", "department",
                      "level", "hire_date", "exit_date", "status", "bank_account"],
    "salary_changes.csv": ["change_id", "employee_id", "effective_date", "annual_salary", "currency",
                           "previous_salary", "reason", "level", "approved_by", "approved_on"],
    "signed_grants.csv": ["grant_id", "employee_id", "shares", "fair_value_per_share_usd", "grant_date",
                          "vest_start", "vest_months", "cliff_months", "board_resolution", "signed_on"],
    "board_approvals.csv": ["resolution_id", "meeting_date", "grant_id"],
    "legacy_payment_history.csv": ["year", "entity", "employee_id", "payee_name", "bank_account", "months_paid",
                                   "provider"],
    "approvals_log.csv": ["request_id", "requested_on", "kind", "entity", "period", "amount", "currency",
                          "description", "requested_by", "approved_by", "approved_on", "status"],
}


@dataclass
class Bundle:
    """Every file of the world, in memory, keyed by path relative to the world root.
    CSV files hold lists of row dicts (all values text); JSON files hold objects."""
    files: dict[str, object] = field(default_factory=dict)

    def rows(self, path: str) -> list[dict]:
        return self.files.setdefault(path, [])

    def months(self) -> list[str]:
        return dates.month_range(C.FIRST_FILE_MONTH, C.OPEN_MONTH)


def _d(value: date | None) -> str:
    return value.isoformat() if value else ""


def hr_census(world: World, key: str) -> list[dict]:
    end = dates.month_end(key)
    rows = []
    for person in world.people:
        if person.hire_date > end:
            continue
        exited = person.exit_date is not None and person.exit_date <= end
        on = person.exit_date if exited else end
        stint = person.stint_on(on)
        rows.append({
            "employee_id": person.employee_id, "first_name": person.first_name, "last_name": person.last_name,
            "email": person.email, "entity": stint.entity, "entity_since": _d(stint.start),
            "department": person.department, "level": str(person.level_on(on)),
            "hire_date": _d(person.hire_date), "exit_date": _d(person.exit_date) if exited else "",
            "status": "left" if exited else "active", "bank_account": stint.bank_account,
        })
    return rows


def _salary_rows(world: World) -> list[dict]:
    last = dates.month_end(C.OPEN_MONTH)
    return [{
        "change_id": c.change_id, "employee_id": c.employee_id, "effective_date": _d(c.effective_date),
        "annual_salary": fmt(c.annual_salary), "currency": c.currency,
        "previous_salary": fmt(c.previous_salary) if c.previous_salary is not None else "",
        "reason": c.reason, "level": str(c.level), "approved_by": c.approved_by, "approved_on": _d(c.approved_on),
    } for c in world.salary_changes if c.approved_on <= last]


def _grant_rows(world: World) -> list[dict]:
    last = dates.month_end(C.OPEN_MONTH)
    return [{
        "grant_id": g.grant_id, "employee_id": g.employee_id, "shares": str(g.shares),
        "fair_value_per_share_usd": f"{g.fair_value_per_share:.2f}", "grant_date": _d(g.grant_date),
        "vest_start": _d(g.vest_start), "vest_months": str(g.vest_months), "cliff_months": str(g.cliff_months),
        "board_resolution": g.board_resolution, "signed_on": _d(g.signed_on),
    } for g in world.grants if g.signed_on and g.signed_on <= last]


def _board_rows(world: World) -> list[dict]:
    return [{"resolution_id": m.resolution_id, "meeting_date": _d(m.meeting_date), "grant_id": grant_id}
            for m in world.board_meetings for grant_id in m.grant_ids]


def _legacy_history(world: World) -> list[dict]:
    """Payee export from the previous payroll provider, before the current files begin."""
    rows = []
    for year in range(int(C.FOUNDED[:4]), int(C.PAYROLL_START[:4])):
        for person in world.people:
            for stint in person.stints:
                months = [k for k in dates.month_range(f"{year}-01", f"{year}-12")
                          if dates.overlap_days(stint.start, stint.end, k)]
                if months:
                    rows.append({"year": str(year), "entity": stint.entity, "employee_id": person.employee_id,
                                 "payee_name": person.name, "bank_account": stint.bank_account,
                                 "months_paid": str(len(months)), "provider": "Previous payroll provider"})
    return rows


def _directory_email(role: str) -> str:
    name = next(d["name"] for d in C.DIRECTORY if d["role"] == role)
    return name.lower().replace(" ", ".") + "@" + C.DOMAIN


def _routine_emails(months: list[str]) -> list[dict]:
    emails = []
    for key in months:
        pay_day = dates.last_business_day(key)
        cutoff = dates.month_start(key) + timedelta(days=14)
        emails.append({
            "received_at": f"{dates.month_start(key) + timedelta(days=2)}T09:12:00",
            "from": _directory_email("finance_manager"), "to": "finance-team@" + C.DOMAIN,
            "subject": f"Payroll cutoff for {key}",
            "body": f"Hi all, payroll changes for {key} need to reach the payroll provider by {cutoff}. "
                    f"Pay day is {pay_day}. Thanks, Daniel"})
        emails.append({
            "received_at": f"{dates.month_start(key) + timedelta(days=5)}T14:40:00",
            "from": _directory_email("hr"), "to": "finance-team@" + C.DOMAIN,
            "subject": f"People update {key}",
            "body": "Starters, leavers and transfers for the month are in this month's HR census. "
                    "Please flag anything that looks off. Priya"})
        emails.append({
            "received_at": f"{pay_day - timedelta(days=3)}T11:05:00",
            "from": _directory_email("treasury"), "to": "finance-team@" + C.DOMAIN,
            "subject": f"Payment run {key}",
            "body": f"The net pay batch for {key} is scheduled for release on {pay_day}, once the payment file "
                    "is approved. Lena"})
    return emails


def _rulings(world: World, rng: random.Random, emails: list[dict]) -> list[dict]:
    """Decisions from earlier closes. Subjects are drawn at random from people employed then."""
    def pick(key: str) -> Person:
        employed = [p for p in world.people if p.employed_on(dates.month_end(key))]
        return rng.choice(employed)

    rulings = []
    bank_month = dates.add_months(C.OPEN_MONTH, -7)
    subject = pick(bank_month)
    received = dates.last_business_day(bank_month) - timedelta(days=4)
    emails.append({
        "received_at": f"{received}T07:51:00",
        "from": f"{subject.first_name.lower()}.{subject.last_name.lower()}.home@mailbox-secure.net",
        "to": "payroll@" + C.DOMAIN,
        "subject": "Change of bank details before pay day",
        "body": f"Hello, this is {subject.name}. I have switched banks; please pay my salary this month into the "
                "new account below. It is urgent, I cannot take calls at the moment. "
                f"New account: {world.person(subject.employee_id).stint_on(received).bank_account[:-4]}9921"})
    rulings.append({
        "ruling_id": "RUL-0001", "period": bank_month, "decided_on": _d(received + timedelta(days=2)),
        "kind": "bank_change_by_email", "subject_employee_id": subject.employee_id,
        "summary": "An email from an outside address asked to redirect an employee's pay to a new account days "
                   "before pay day. HR could not confirm it by a call-back to the number on file.",
        "decision": "rejected", "decided_by": next(d["name"] for d in C.DIRECTORY if d["role"] == "controller"),
        "rules_cited": ["PAY-06"],
        "precedent": "Bank detail changes arriving by email are never applied without HR confirmation and a "
                     "call-back; the payment is held until then."})

    fx_month = dates.add_months(C.OPEN_MONTH, -9)
    rulings.append({
        "ruling_id": "RUL-0002", "period": fx_month, "decided_on": _d(dates.month_end(fx_month) + timedelta(days=6)),
        "kind": "fx_difference_below_materiality", "subject_employee_id": "",
        "summary": "Stock compensation translated by the equity platform differed from the ledger by a few units "
                   "of currency because of a different rate source.",
        "decision": "explained", "decided_by": next(d["name"] for d in C.DIRECTORY if d["role"] == "finance_manager"),
        "rules_cited": ["FIN-01", "FIN-02"],
        "precedent": "FX differences below materiality are explained and logged; the ledger keeps the policy rate."})

    raise_month = dates.add_months(C.OPEN_MONTH, -5)
    subject = pick(raise_month)
    rulings.append({
        "ruling_id": "RUL-0003", "period": raise_month,
        "decided_on": _d(dates.month_end(raise_month) + timedelta(days=4)),
        "kind": "backdated_salary_change", "subject_employee_id": subject.employee_id,
        "summary": "A salary change approved late had an effective date in an already closed month.",
        "decision": "approved_with_conditions", "decided_by": next(d["name"] for d in C.DIRECTORY if d["role"] == "controller"),
        "rules_cited": ["PAY-03", "FIN-05"],
        "precedent": "Arrears for closed months are paid and booked in the open month only, after approval; "
                     "closed months are not reopened for this."})
    return rulings


def _approvals_log(months: list[str], rng: random.Random) -> list[dict]:
    """Manual journal requests from earlier closes (bonus and holiday accruals), properly approved."""
    rows, directory = [], {d["role"]: d["name"] for d in C.DIRECTORY}
    for key in months[:-1]:
        entity = rng.choice(list(C.ENTITIES))
        amount = rng.randint(40, 300) * 100
        rows.append({
            "request_id": f"AR-{len(rows) + 1:04d}", "requested_on": _d(dates.month_end(key)),
            "kind": "accrual", "entity": entity, "period": key, "amount": fmt(amount),
            "currency": C.ENTITIES[entity]["currency"], "description": "Holiday pay accrual true-up",
            "requested_by": directory["finance_manager"], "approved_by": directory["controller"],
            "approved_on": _d(dates.month_end(key) + timedelta(days=3)), "status": "approved"})
    return rows


def standing_documents(world: World, rng: random.Random) -> dict[str, object]:
    months = dates.month_range(C.FIRST_FILE_MONTH, C.OPEN_MONTH)
    emails = _routine_emails(months)
    rulings = _rulings(world, rng, emails)
    emails.sort(key=lambda e: e["received_at"])
    for n, email in enumerate(emails, 1):
        email["email_id"] = f"EM-{n:05d}"
    return {
        "standing/company.json": {
            "name": C.COMPANY, "founded": C.FOUNDED, "functional_currency_by_entity":
                {k: v["currency"] for k, v in C.ENTITIES.items()},
            "entities": [{"entity": k, **v} for k, v in C.ENTITIES.items()], "departments": C.DEPARTMENTS,
            "first_file_month": C.FIRST_FILE_MONTH, "open_month": C.OPEN_MONTH},
        "standing/directory.json": {"people": C.DIRECTORY},
        "standing/pay_policy.json": PAY_POLICY,
        "standing/finance_policy.json": FINANCE_POLICY,
        "standing/salary_changes.csv": _salary_rows(world),
        "standing/signed_grants.csv": _grant_rows(world),
        "standing/board_approvals.csv": _board_rows(world),
        "standing/legacy_payment_history.csv": _legacy_history(world),
        "standing/approvals_log.csv": _approvals_log(months, rng),
        "standing/rulings.json": {"rulings": rulings},
        "standing/inbox.json": {"emails": [{"email_id": e["email_id"], **{k: v for k, v in e.items() if k != "email_id"}}
                                           for e in emails]},
    }
