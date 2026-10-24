"""Splitting an amount across lines without losing or inventing a cent.

Rule (T5): each line gets its share rounded half up to the cent; whatever is left over goes to
one fixed line, the first line in the fixed order that has a share. The fixed orders are the
company's department order and entity order, so the same split always lands the same way."""
from __future__ import annotations

from decimal import Decimal
from typing import Hashable, Iterable, Mapping

from vouch.calc import fx
from vouch.money import ZERO, cents

DEPARTMENT_ORDER = ("Engineering", "Product", "GTM", "G&A")
ENTITY_ORDER = ("US", "UK", "DE")


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


def translate_split(usd_by_line: Mapping[Hashable, Decimal], currency: str, month: str,
                    order: Iterable[Hashable]) -> tuple[dict, fx.Translation]:
    """Translate the total once at the month's rate, then split it by the USD weights.
    The lines always add up to the translated total."""
    total = sum((Decimal(v) for v in usd_by_line.values()), ZERO)
    translation = fx.translate(total, currency, month)
    if translation.amount_local == 0:
        return {k: ZERO for k in usd_by_line}, translation
    return allocate(translation.amount_local, usd_by_line, order), translation
