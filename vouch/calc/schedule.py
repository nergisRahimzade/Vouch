"""When costs land and when money is due: the schedule idea of v1's vesting, for event payables.
A deposit is released to expense in full on the event's last day; an accrual sits at month end and
reverses on the 1st of the next month; a bill is due at its invoice date plus terms, and an early-pay
discount applies only if approval lands inside its window (spec §5.9)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from vouch import dates
from vouch.money import ZERO, cents

TERMS = re.compile(r"(?:(?P<pct>\d+(?:\.\d+)?)\s*/\s*(?P<ddays>\d+)\s*)?net[\s_]*(?P<net>\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Terms:
    net_days: int
    discount_pct: Decimal = ZERO          # a fraction: 0.02 is 2%
    discount_days: int = 0
    source: str = ""                      # the contract terms or clause they come from


def parse_terms(text: str | None, early_pay: dict | None = None, source: str = "") -> Terms:
    """'net_30', 'net 30', '2/10 net 30'; an early-pay clause's params override what the text says."""
    match = TERMS.search(str(text or ""))
    net = int(match.group("net")) if match else 30
    pct = Decimal(match.group("pct")) / 100 if match and match.group("pct") else ZERO
    ddays = int(match.group("ddays")) if match and match.group("ddays") else 0
    if early_pay:
        pct = Decimal(str(early_pay.get("discount_pct", pct)))
        pct = pct / 100 if pct > 1 else pct
        ddays = int(early_pay.get("discount_days", ddays))
        net = int(early_pay.get("net_days", net))
    return Terms(net, pct, ddays, source)


def pay_by(invoice_date: str, terms: Terms) -> date:
    return date.fromisoformat(str(invoice_date)[:10]) + timedelta(days=terms.net_days)


def discount_by(invoice_date: str, terms: Terms) -> date | None:
    if not terms.discount_pct or not terms.discount_days:
        return None
    return date.fromisoformat(str(invoice_date)[:10]) + timedelta(days=terms.discount_days)


@dataclass(frozen=True)
class Proposal:
    amount: Decimal                       # the cash to pay
    discount: Decimal
    pay_by: date
    account_last4: str
    full: Decimal                         # the approved amount the payment settles

    def to_dict(self) -> dict:
        return {"amount": f"{self.amount:.2f}", "pay_by": self.pay_by.isoformat(), "discount": f"{self.discount:.2f}",
                "account_last4": self.account_last4}


def payment_proposal(approved: Decimal, invoice_date: str, terms: Terms, approved_on: date | None,
                     account_last4: str) -> Proposal:
    """Pay-by = invoice date + terms. With an early-pay discount and approval inside its window, pay by the
    discount date and take the discount. The account is always the active account on file."""
    approved = cents(Decimal(approved))
    window = discount_by(invoice_date, terms)
    if window and approved_on and approved_on <= window:
        discount = cents(approved * terms.discount_pct)
        return Proposal(approved - discount, discount, window, account_last4, approved)
    return Proposal(approved, ZERO, pay_by(invoice_date, terms), account_last4, approved)


def release_date(event: dict) -> date:
    """A deposit is released to expense on the event's last day."""
    return date.fromisoformat(str(event["end_date"])[:10])


def accrual_date(period: str) -> date:
    return dates.month_end(period)


def reversal_date(period: str) -> date:
    """An accrual reverses automatically on the 1st of the next month."""
    return dates.month_start(dates.add_months(period, 1))
