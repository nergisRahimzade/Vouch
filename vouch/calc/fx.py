"""Recorded USD exchange rates (Open Exchange Rates snapshot). A month is translated at
its average rate (policy FIN-02); a month missing any business day has no rate."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

from vouch import dates
from vouch.money import cents

SNAPSHOT = Path(__file__).resolve().parents[1] / "reference" / "fx_oxr_usd.csv"
SOURCE = "Open Exchange Rates, USD base, daily historical rates"


class MissingFxRate(LookupError):
    """No complete set of recorded rates for the month. The conversion must be held."""


@dataclass(frozen=True)
class FxRate:
    currency: str
    month: str
    rate: Decimal          # units of currency per 1 USD
    method: str
    days: int
    first_date: str
    last_date: str

    @property
    def citation(self) -> str:
        if self.currency == "USD":
            return f"USD, no translation ({self.month})"
        return (f"USD->{self.currency} {self.rate} = {self.method} for {self.month} "
                f"({self.days} business days {self.first_date}..{self.last_date}; {SOURCE})")


@dataclass(frozen=True)
class Translation:
    """A converted amount that names its rate and the dates the rate comes from."""
    amount_usd: Decimal
    amount_local: Decimal
    kind: str              # expense | balance (both use the month's average rate, policy FIN-02)
    rate: FxRate

    @property
    def basis(self) -> str:
        return self.rate.citation


@lru_cache(maxsize=1)
def _daily() -> dict[str, dict[str, str]]:
    with SNAPSHOT.open(newline="", encoding="utf-8") as f:
        return {row["date"]: row for row in csv.DictReader(f)}


def business_days(month: str) -> list[str]:
    d, end, out = dates.month_start(month), dates.month_end(month), []
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def month_average(currency: str, month: str) -> FxRate:
    if currency == "USD":
        return FxRate("USD", month, Decimal(1), "identity", 0, "", "")
    days = business_days(month)
    daily = _daily()
    missing = [d for d in days if d not in daily]
    if missing:
        raise MissingFxRate(f"USD->{currency} for {month}: {len(missing)} business days have no recorded rate "
                            f"(first missing {missing[0]})")
    total = sum((Decimal(daily[d][currency]) for d in days), Decimal(0))
    rate = (total / len(days)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return FxRate(currency, month, rate, "monthly average of business-day rates", len(days), days[0], days[-1])


def to_local(amount_usd: Decimal, rate: FxRate) -> Decimal:
    return cents(amount_usd * rate.rate)


def translate(amount_usd: Decimal, currency: str, month: str, kind: str = "expense") -> Translation:
    """USD -> local at the month's average rate. Policy FIN-02 uses one rate for expenses and
    balances alike; a month without every business day recorded raises MissingFxRate."""
    if kind not in ("expense", "balance"):
        raise ValueError(f"unknown translation kind {kind!r}")
    rate = month_average(currency, month)
    return Translation(amount_usd, to_local(amount_usd, rate), kind, rate)
