"""Loads statutory rates from tax_rates.json. Every lookup names its source."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

RATES_FILE = Path(__file__).with_name("tax_rates.json")


class MissingRate(LookupError):
    """A rate needed for a calculation is not in the data file. Never guessed."""


@dataclass(frozen=True)
class Rate:
    entity: str
    tax_year: int
    name: str
    value: object
    source: str
    checked: str

    @property
    def citation(self) -> str:
        return f"{self.entity} {self.tax_year} {self.name}: {self.source}"


@lru_cache(maxsize=1)
def _table() -> dict:
    return json.loads(RATES_FILE.read_text(encoding="utf-8"))


def rate(entity: str, tax_year: int, name: str) -> Rate:
    try:
        entry = _table()[entity][str(tax_year)][name]
    except KeyError:
        raise MissingRate(f"no {name} for {entity} tax year {tax_year} in {RATES_FILE.name}") from None
    return Rate(entity, tax_year, name, entry["value"], entry["source"], entry["checked"])


def dec(entity: str, tax_year: int, name: str) -> Decimal:
    return Decimal(rate(entity, tax_year, name).value)


def tax_years(entity: str) -> list[int]:
    return sorted(int(y) for y in _table()[entity] if not y.startswith("_"))
