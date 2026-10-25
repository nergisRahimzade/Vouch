"""One entry point for the company world: the base fixtures, or a scenario on top of them, as they
stand on one date. The system of record (vouch.db) loads what this returns."""
from __future__ import annotations

from collections import Counter

from vouch.world.scenario import Scenario, rows_for


def build(scenario: Scenario | None = None, as_of: str | None = None) -> tuple[dict, dict]:
    """(rows by table, meta). The same inputs always give the same rows."""
    return rows_for(scenario, as_of)


def summary(rows: dict, meta: dict) -> dict:
    return {"as_of": meta["as_of"], "scenario": meta["scenario_id"],
            "rows": {t: len(r) for t, r in rows.items() if r},
            "invoices_by_vendor": dict(Counter(i["vendor"] for i in rows["invoices"]))}
