"""T5: splits put the leftover cent on one fixed line; no entry is off by a cent."""
import random
from collections import defaultdict
from decimal import Decimal as D

import pytest

from vouch.calc.rounding import DEPARTMENT_ORDER, ENTITY_ORDER, allocate, translate_split


def test_three_way_split_puts_the_leftover_cent_on_the_fixed_line():
    shares = allocate(D("100.00"), {"Engineering": 1, "Product": 1, "GTM": 1}, DEPARTMENT_ORDER)
    assert shares == {"Engineering": D("33.34"), "Product": D("33.33"), "GTM": D("33.33")}


def test_fixed_line_is_the_first_in_order_with_a_share():
    # each third of 0.02 rounds to 0.01 (0.03 in all); the -0.01 left over goes to Product,
    # the first line in the fixed order that has a share (Engineering's weight is zero)
    shares = allocate(D("0.02"), {"G&A": 1, "GTM": 1, "Engineering": 0, "Product": 1}, DEPARTMENT_ORDER)
    assert shares == {"G&A": D("0.01"), "GTM": D("0.01"), "Engineering": D("0.00"), "Product": D("0.00")}
    by_entity = allocate(D("10.00"), {"DE": 1, "UK": 1, "US": 1}, ENTITY_ORDER)
    assert by_entity["US"] == D("3.34")


def test_negative_amounts_and_weights_split_exactly():
    shares = allocate(D("-250.01"), {"Engineering": D("-3"), "GTM": D("-7")}, DEPARTMENT_ORDER)
    assert sum(shares.values()) == D("-250.01")


def test_random_splits_never_lose_or_invent_a_cent():
    rng = random.Random(7)
    for _ in range(2000):
        lines = rng.sample(DEPARTMENT_ORDER, rng.randint(1, 4))
        weights = {k: D(rng.randint(1, 10**6)) / 100 for k in lines}
        total = D(rng.randint(-10**8, 10**8)) / 100
        shares = allocate(total, weights, DEPARTMENT_ORDER)
        assert sum(shares.values()) == total
        first = next(k for k in DEPARTMENT_ORDER if k in weights)
        exact = {k: total * weights[k] / sum(weights.values()) for k in weights}
        assert all(abs(shares[k] - exact[k]) <= D("0.005") for k in weights if k != first)


def test_translated_split_adds_up_to_the_translated_total():
    usd = {"Engineering": D("1234.56"), "Product": D("789.01"), "GTM": D("333.33"), "G&A": D("0.01")}
    lines, translation = translate_split(usd, "GBP", "2026-04", DEPARTMENT_ORDER)
    assert sum(lines.values()) == translation.amount_local
    assert translation.amount_usd == sum(usd.values())


def test_bad_splits_are_refused():
    with pytest.raises(ValueError):
        allocate(D("1.00"), {"Engineering": 0}, DEPARTMENT_ORDER)
    with pytest.raises(ValueError):
        allocate(D("1.00"), {"Marketing": 1}, DEPARTMENT_ORDER)


def test_no_entry_in_the_ledger_is_off_by_a_cent(db):
    totals = defaultdict(D)
    for r in db.execute("SELECT entry_id, debit, credit FROM journal_lines"):
        totals[r["entry_id"]] += D(r["debit"]) - D(r["credit"])
    assert totals and all(v == 0 for v in totals.values())
