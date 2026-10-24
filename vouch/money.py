"""Exact decimal helpers. No float ever touches a money amount."""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def D(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError("floats are not allowed for money; pass a string or int")
    return Decimal(str(value))


def cents(value: Decimal) -> Decimal:
    """Round half up to the cent (commercial rounding)."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def cents_down(value: Decimal) -> Decimal:
    return D(value).quantize(CENT, rounding=ROUND_DOWN)


def whole_down(value: Decimal) -> Decimal:
    return D(value).quantize(Decimal("1"), rounding=ROUND_DOWN)


def fmt(value: Decimal) -> str:
    """Canonical text form of an amount in files and the database."""
    return f"{cents(value):.2f}"
