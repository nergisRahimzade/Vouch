"""Noise: ordinary mess a real close sees, which must be explained, not flagged.
Each item is recorded so the evaluator can count anything wrongly held as a false hold."""
from __future__ import annotations

import random
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from vouch import dates
from vouch.money import ZERO, cents, fmt
from vouch.world import config as C
from vouch.world.payroll import MonthContext
from vouch.world.plants import OPEN, Plant, Planter, month, renumber_bank, row_of
from vouch.world.policies import FINANCE_POLICY


def _typo(word: str, rng: random.Random) -> str:
    i = rng.randrange(1, len(word) - 1)
    choice = rng.randrange(3)
    if choice == 0:                                  # swapped letters
        return word[:i - 1] + word[i] + word[i - 1] + word[i + 1:]
    if choice == 1:                                  # dropped letter
        return word[:i] + word[i + 1:]
    return word[:i] + word[i] + word[i:]             # doubled letter


def email_typos(noise_id: str, planter: Planter) -> Plant:
    """Typos in routine emails from colleagues."""
    plant = Plant(noise_id, "email_typos", C.FIRST_FILE_MONTH)

    @plant.on(C.FIRST_FILE_MONTH, "hr")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(noise_id)
        emails = [e for e in ctx.bundle.files["standing/inbox.json"]["emails"]
                  if e["from"].endswith("@" + C.DOMAIN)]
        touched = []
        for email in rng.sample(emails, 6):
            words = email["body"].split(" ")
            for i in rng.sample([i for i, w in enumerate(words) if len(w) > 4 and w.isalpha()], 2):
                words[i] = _typo(words[i], rng)
            email["body"] = " ".join(words)
            touched.append(email["email_id"])
        plant.key.update(noise_id=noise_id, kind=plant.kind, month="various", subject={"email_ids": sorted(touched)},
                         expected_outcome="explain_not_flag",
                         explanation="Spelling mistakes in genuine internal emails change no fact.")
    return plant


def duplicate_hr_row(noise_id: str, planter: Planter) -> Plant:
    """The HR export repeats one person's row exactly."""
    plant = Plant(noise_id, "duplicate_hr_row", OPEN)

    @plant.on(OPEN, "hr")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(noise_id)
        census = ctx.rows("hr_census.csv")
        active = [r for r in census if r["status"] == "active" and r["employee_id"] not in planter.claimed]
        row = rng.choice(active)
        planter.claim(row["employee_id"])
        census.insert(census.index(row) + 1, dict(row))
        plant.key.update(noise_id=noise_id, kind=plant.kind, month=OPEN,
                         subject={"employee_id": row["employee_id"], "file": ctx.path("hr_census.csv")},
                         expected_outcome="explain_not_flag",
                         explanation="An exact duplicate row in the HR export is one person; deduplicate and note it.")
    return plant


def bank_line_after_month_end(noise_id: str, planter: Planter) -> Plant:
    """One salary payment released on pay day clears the bank on the first business day of the next month."""
    run_month = month(-5)
    plant = Plant(noise_id, "bank_line_posted_after_month_end", run_month)

    @plant.on(run_month, "bank")
    def _(ctx: MonthContext) -> None:
        rng = planter.rng(noise_id)
        entity = "DE"
        bank = ctx.rows(f"bank_statement_{entity}.csv")
        payees = {p["payment_id"]: p["employee_id"] for p in ctx.rows(f"payment_file_{entity}.csv")}
        line = rng.choice([b for b in bank if b["reference"].startswith("PAY-")
                           and payees[b["reference"]] not in planter.claimed])
        planter.claim(payees[line["reference"]])
        bank.remove(line)
        renumber_bank(bank, entity, run_month)
        posted = dates.month_end(run_month) + timedelta(days=1)
        while posted.weekday() >= 5:
            posted += timedelta(days=1)
        ctx.state.bank_carry[entity].append({k: line[k] for k in ("description", "reference", "amount")}
                                            | {"date": posted.isoformat()})
        plant.key.update(noise_id=noise_id, kind=plant.kind, month=run_month,
                         subject={"entity": entity, "payment_id": line["reference"], "value_date": line["date"],
                                  "posted": posted.isoformat()},
                         expected_outcome="explain_not_flag",
                         explanation="Timing difference: released on pay day, cleared on the next business day.",
                         amount={"value": fmt(-Decimal(line["amount"])), "currency": "EUR"})
    return plant


def fx_difference_within_tolerance(noise_id: str, planter: Planter) -> Plant:
    """The equity platform translates the open month at a 4-decimal rate instead of the policy rate."""
    plant = Plant(noise_id, "fx_difference_within_tolerance", OPEN)

    @plant.on(OPEN, "equity")
    def _(ctx: MonthContext) -> None:
        entity = "DE"
        rows = [r for r in ctx.rows("equity_report.csv") if r["entity"] == entity]
        policy_rate = Decimal(rows[0]["fx_rate"])
        platform_rate = policy_rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        difference = ZERO
        for row in rows:
            expense = Decimal(row["expense_month_usd"])
            new_local = cents(expense * platform_rate)
            difference += new_local - Decimal(row["expense_month_local"])
            row.update({"fx_rate": str(platform_rate), "expense_month_local": fmt(new_local)})
        threshold = Decimal(next(r for r in FINANCE_POLICY["rules"] if r["rule_id"] == "FIN-01")["threshold"]["EUR"])
        assert abs(difference) < threshold, "FX noise must stay inside materiality"
        plant.key.update(noise_id=noise_id, kind=plant.kind, month=OPEN,
                         subject={"entity": entity, "file": ctx.path("equity_report.csv"),
                                  "platform_rate": str(platform_rate), "policy_rate": str(policy_rate)},
                         expected_outcome="explain_not_flag",
                         explanation="Platform used a rounded rate; total difference is below materiality (FIN-01).",
                         amount={"value": fmt(difference), "currency": "EUR"})
    return plant


NOISE_SET = [
    ("NOISE-01", email_typos),
    ("NOISE-02", duplicate_hr_row),
    ("NOISE-03", bank_line_after_month_end),
    ("NOISE-04", fx_difference_within_tolerance),
]


def noise_set(planter: Planter) -> list[Plant]:
    return [factory(noise_id, planter) for noise_id, factory in NOISE_SET]
