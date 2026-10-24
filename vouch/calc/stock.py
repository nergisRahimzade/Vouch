"""ASC 718 stock compensation: grant-date fair value expensed straight-line over the
service period; forfeiture reverses expense on unvested awards; nothing is expensed
before board approval (the grant date)."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vouch import dates
from vouch.money import ZERO, cents


@dataclass(frozen=True)
class GrantTerms:
    shares: int
    fair_value_per_share: Decimal | None   # USD at grant date; None while unapproved
    grant_date: date | None                # board approval date
    vest_start: date
    vest_months: int
    cliff_months: int


@dataclass(frozen=True)
class MonthExpense:
    status: str               # pending_approval | active | fully_vested | forfeited | vested_and_left
    service_months: int
    vested_shares: int
    expense: Decimal          # USD recognised in the month (negative = reversal)
    cumulative: Decimal       # USD recognised to the end of the month


def service_months(vest_start: date, as_of: date) -> int:
    """Monthly anniversaries of vest_start reached on or before as_of."""
    months = (as_of.year - vest_start.year) * 12 + as_of.month - vest_start.month
    anniversary_day = min(vest_start.day, calendar.monthrange(as_of.year, as_of.month)[1])
    if as_of.day < anniversary_day:
        months -= 1
    return max(0, months)


def total_value(terms: GrantTerms) -> Decimal:
    return cents(terms.fair_value_per_share * terms.shares) if terms.fair_value_per_share is not None else ZERO


def vested_shares(terms: GrantTerms, months: int) -> int:
    if months < terms.cliff_months:
        return 0
    return terms.shares * min(months, terms.vest_months) // terms.vest_months


def cumulative_expense(terms: GrantTerms, exit_date: date | None, as_of: date) -> tuple[str, int, int, Decimal]:
    """(status, service months, vested shares, cumulative USD expense) at as_of."""
    if terms.grant_date is None or terms.grant_date > as_of:
        return "pending_approval", service_months(terms.vest_start, as_of), 0, ZERO
    left = exit_date is not None and exit_date <= as_of
    months = min(service_months(terms.vest_start, exit_date if left else as_of), terms.vest_months)
    vested = vested_shares(terms, months)
    if left:
        # Forfeiture: only the vested part keeps its expense. Before the cliff nothing has vested.
        expensed_months = months if months >= terms.cliff_months else 0
        status = "vested_and_left" if months >= terms.vest_months else "forfeited"
    else:
        expensed_months = months
        status = "fully_vested" if months >= terms.vest_months else "active"
    return status, months, vested, cents(total_value(terms) * expensed_months / terms.vest_months)


def month_expense(terms: GrantTerms, exit_date: date | None, month: str) -> MonthExpense:
    status, months, vested, cumulative = cumulative_expense(terms, exit_date, dates.month_end(month))
    prior = cumulative_expense(terms, exit_date, dates.month_end(dates.add_months(month, -1)))[3]
    return MonthExpense(status, months, vested, cumulative - prior, cumulative)
