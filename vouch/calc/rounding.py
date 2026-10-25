"""Splitting an amount across lines without losing or inventing a cent.

Rule: each line gets its share rounded half up to the cent; whatever is left over goes to one fixed
line, the first line in the fixed order that has a share. The fixed order is the chart of accounts,
so the same split always lands the same way."""
from __future__ import annotations

from decimal import Decimal
from typing import Hashable, Iterable, Mapping

from vouch.db.accounts import CHART
from vouch.money import ZERO, cents

ACCOUNT_ORDER = tuple(a.code for a in CHART)


def fixed_line(weights: Mapping[Hashable, Decimal], order: Iterable[Hashable]) -> Hashable:
    order = list(order)
    unknown = [k for k in weights if k not in order]
    if unknown:
        raise ValueError(f"lines {unknown} are not in the fixed order {order}")
    for key in order:
        if weights.get(key, ZERO) != 0:
            return key
    raise ValueError("nothing to split: every weight is zero")


def allocate(total: Decimal, weights: Mapping[Hashable, Decimal], order: Iterable[Hashable]) -> dict:
    """Split `total` in proportion to `weights` (which may be negative), exact to the cent."""
    total = cents(Decimal(total))
    if total != Decimal(total):
        raise ValueError("total must be in whole cents")
    base = sum((Decimal(w) for w in weights.values()), ZERO)
    if base == 0:
        raise ValueError("weights add up to zero; nothing to split by")
    shares = {k: cents(total * Decimal(w) / base) for k, w in weights.items()}
    shares[fixed_line(weights, order)] += total - sum(shares.values(), ZERO)
    return shares
