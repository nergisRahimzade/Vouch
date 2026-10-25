"""Currency: GBP converts to USD at the invoice month's period rate (frozen 1.27, simulated); a missing
month falls back to the last known rate with a warning (spec §8.3); thresholds use the USD amount."""
from decimal import Decimal as D

import pytest

from vouch.calc.fx import MissingFxRate, to_usd

RATES = {("GBP/USD", f"2026-{m:02d}"): D("1.27") for m in range(1, 13)}


def test_gbp_converts_at_the_period_rate():
    # P07: approved 15,242.70 x 1.27 = 19,358.229 -> 19,358.23; disputed 1,871.10 x 1.27 = 2,376.297 -> 2,376.30.
    assert to_usd(D("15242.70"), "GBP", "2026-09", RATES).amount_usd == D("19358.23")
    assert to_usd(D("1871.10"), "GBP", "2026-09", RATES).amount_usd == D("2376.30")
    assert to_usd(D("31920.00"), "GBP", "2026-09", RATES).basis == "GBP/USD 1.27 (period rate 2026-09)"


def test_usd_needs_no_rate():
    conversion = to_usd(D("100.00"), "USD", "2030-01", {})
    assert (conversion.amount_usd, conversion.warning) == (D("100.00"), "")


def test_a_missing_month_uses_the_last_known_rate_and_says_so():
    conversion = to_usd(D("100.00"), "GBP", "2027-02", RATES)
    assert conversion.amount_usd == D("127.00") and conversion.period == "2026-12"
    assert "last known rate" in conversion.warning


def test_no_rate_at_all_is_refused():
    with pytest.raises(MissingFxRate):
        to_usd(D("100.00"), "GBP", "2025-06", RATES)
