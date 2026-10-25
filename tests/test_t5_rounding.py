"""Splits put the leftover cent on one fixed line; no entry is off by a cent."""
import random
from decimal import Decimal as D

from vouch.calc.rounding import ACCOUNT_ORDER, allocate


def test_three_way_split_puts_the_leftover_cent_on_the_fixed_line():
    shares = allocate(D("100.00"), {"6100": 1, "6110": 1, "6120": 1}, ACCOUNT_ORDER)
    assert shares == {"6100": D("33.34"), "6110": D("33.33"), "6120": D("33.33")}


def test_fixed_line_is_the_first_in_order_with_a_share():
    shares = allocate(D("0.02"), {"6140": 1, "6130": 1, "6100": 0, "6110": 1}, ACCOUNT_ORDER)
    assert shares == {"6140": D("0.01"), "6130": D("0.01"), "6100": D("0.00"), "6110": D("0.00")}


def test_random_splits_never_lose_or_invent_a_cent():
    rng = random.Random(7)
    for _ in range(2000):
        lines = rng.sample(ACCOUNT_ORDER, rng.randint(1, 5))
        weights = {k: D(rng.randint(1, 10**6)) / 100 for k in lines}
        total = D(rng.randint(-10**8, 10**8)) / 100
        shares = allocate(total, weights, ACCOUNT_ORDER)
        assert sum(shares.values()) == total
