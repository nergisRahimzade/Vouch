"""Period exchange rates (`period_rates`; GBP/USD frozen at 1.27 for 2026, simulated). A GBP invoice is
converted to USD at its month's rate for approval thresholds. A month with no rate uses the last known
earlier month's rate and says so in a warning (spec §8.3); with no earlier rate at all, it raises."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vouch.money import cents


class MissingFxRate(LookupError):
    """No rate for the month and none before it."""


@dataclass(frozen=True)
class Conversion:
    """A converted amount that names its rate, the period the rate is for, and any warning."""
    amount: Decimal
    currency: str
    amount_usd: Decimal
    rate: Decimal          # USD per one unit of `currency`
    period: str            # the period the rate was taken from
    warning: str = ""

    @property
    def basis(self) -> str:
        if self.currency == "USD":
            return "USD, no conversion"
        return f"{self.currency}/USD {self.rate} (period rate {self.period})"


def rate(rates: dict[tuple[str, str], Decimal], currency: str, period: str) -> tuple[Decimal, str, str]:
    """(rate, period it is for, warning). `rates` maps (pair, period) -> USD per unit, e.g. ("GBP/USD", "2026-09")."""
    if currency == "USD":
        return Decimal(1), period, ""
    pair = f"{currency}/USD"
    if (pair, period) in rates:
        return rates[(pair, period)], period, ""
    earlier = sorted(p for (q, p) in rates if q == pair and p < period)
    if not earlier:
        raise MissingFxRate(f"no {pair} rate for {period} or any month before it")
    last = earlier[-1]
    return rates[(pair, last)], last, f"no {pair} rate for {period}; used the last known rate, for {last}"


def to_usd(amount: Decimal, currency: str, period: str, rates: dict[tuple[str, str], Decimal]) -> Conversion:
    value, used, warning = rate(rates, currency, period)
    return Conversion(Decimal(amount), currency, cents(Decimal(amount) * value), value, used, warning)
