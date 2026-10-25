"""What a live run costs: an estimate from what this database has actually measured, and the default
spending cap per run. Every figure names where it came from; nothing is typed in as if it were a result."""
from __future__ import annotations

import sqlite3
from decimal import Decimal

# One live close of the main set through the dashboard on 2026-10-24: 14 cases, claude-opus-5-5, effort
# medium, no conversation caching, RUN-001 (agent_runs.cost_usd). Replaced by the latest live run in the
# same database as soon as there is one.
MEASURED_CLOSE_USD = Decimal("3.4845")
MEASURED_CLOSE_BASIS = "one live close measured on 2026-10-24, before the cost work (claude-opus-5-5, effort medium)"

# The most one run may spend before its remaining cases are held with a plain reason. Above the measured
# cost of a close, so a normal run never meets it.
DEFAULT_BUDGET_USD = Decimal("6.00")
MIN_BUDGET_USD, MAX_BUDGET_USD = Decimal("0.25"), Decimal("50.00")


def clamp_budget(value) -> Decimal:
    budget = Decimal(str(value)).quantize(Decimal("0.01"))
    if not MIN_BUDGET_USD <= budget <= MAX_BUDGET_USD:
        raise ValueError(f"a spending cap is between ${MIN_BUDGET_USD} and ${MAX_BUDGET_USD}")
    return budget


def estimate_close(conn: sqlite3.Connection | None) -> dict:
    """The expected cost of one live close: the latest finished live close in this database, else the
    measured figure above."""
    if conn is not None:
        row = conn.execute("SELECT run_id, cost_usd, started_at FROM agent_runs WHERE mode = 'model' AND "
                           "status = 'done' AND CAST(cost_usd AS REAL) > 0 ORDER BY rowid DESC LIMIT 1").fetchone() \
            if _has_runs(conn) else None
        if row:
            return {"usd": str(Decimal(row["cost_usd"]).quantize(Decimal("0.01"))),
                    "basis": f"the last live close in this database ({row['run_id']}, {row['started_at'][:10]})"}
    return {"usd": str(MEASURED_CLOSE_USD.quantize(Decimal("0.01"))), "basis": MEASURED_CLOSE_BASIS}


def estimate_score(conn: sqlite3.Connection | None, runs: int) -> dict:
    """A score is `runs` full closes on fresh copies of the company (colleagues are scripted and free)."""
    close = estimate_close(conn)
    return {"usd": str((Decimal(close["usd"]) * runs).quantize(Decimal("0.01"))), "runs": runs,
            "basis": f"{runs} closes at {close['basis']}"}


def _has_runs(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'agent_runs'").fetchone() is not None
