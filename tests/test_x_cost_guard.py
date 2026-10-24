"""Cost control, Phase 0: the cost of a live run is estimated from what was measured, shown before it starts,
shown while it runs, and capped. When the cap is reached no further model call is made and what is left is
held with a plain reason: never a guess, never a hang. All of it is checked with a stand-in model client that
bills known token counts, so no test spends money."""
import shutil
import sys
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from vouch.agent import cost
from vouch.agent.policy import ModelPolicy
from vouch.agent.runner import run_close
from vouch.db import connect
from vouch.ui.server import create_app


class _PricedClient:
    """Answers every call with plain text and bills a fixed number of input tokens ($4.00 per million)."""
    def __init__(self, conn=None, input_tokens=1_000_000):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.calls, self.input_tokens, self.conn, self.saved_before_call = 0, input_tokens, conn, []

    def create(self, **kwargs):
        if self.conn is not None:
            row = self.conn.execute("SELECT cost_usd FROM agent_runs ORDER BY rowid DESC LIMIT 1").fetchone()
            self.saved_before_call.append(row[0] if row else None)
        self.calls += 1
        usage = SimpleNamespace(input_tokens=self.input_tokens, output_tokens=0, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text="Nothing further to do on this case.")])


@pytest.fixture
def fresh(db_path, tmp_path):
    path = tmp_path / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    yield conn, path
    conn.close()


def test_the_cap_stops_further_model_calls_and_holds_what_is_left(fresh):
    conn, _ = fresh
    client = _PricedClient()
    result = run_close(conn, ModelPolicy(client=client, budget_usd="6.00"))
    assert client.calls == 2                                   # $4.00, then $8.00 >= $6.00: no third call
    held = conn.execute("SELECT note FROM cases WHERE status = 'held' AND note LIKE '%spending cap%'").fetchall()
    assert len(held) == result["cases"] - 2 > 0
    assert all("no further model call was made" in r[0] for r in held)
    run = conn.execute("SELECT status, report, report_status, cost_usd, budget_usd FROM agent_runs").fetchone()
    assert run["status"] == "failed" and run["report"] is None and run["report_status"] == "not written"
    assert run["cost_usd"] == "8.0000" and run["budget_usd"] == "6.00"     # the real cost, and the cap it ran under
    assert "spending cap" in result["report_problems"][0]
    from vouch.controls import audit
    assert audit.verify(conn).ok


def test_the_cost_so_far_is_saved_while_the_run_is_still_going(fresh):
    conn, _ = fresh
    client = _PricedClient(conn, input_tokens=100_000)           # $0.40 a call, far below the cap
    run_close(conn, ModelPolicy(client=client, budget_usd="6.00"))
    assert client.saved_before_call[0] == "0.00"
    assert client.saved_before_call[1] == "0.4000"                # the first case's cost was saved before the second
    assert Decimal(client.saved_before_call[2]) == Decimal("0.8000")


def test_a_run_without_a_cap_is_allowed_only_when_asked_for(fresh):
    conn, _ = fresh
    assert ModelPolicy(client=_PricedClient()).budget == cost.DEFAULT_BUDGET_USD
    assert ModelPolicy(client=_PricedClient(), budget_usd=None).budget is None


def test_the_estimate_comes_from_the_last_live_run_in_the_database(fresh):
    conn, _ = fresh
    assert cost.estimate_close(None)["usd"] == "3.48" and "measured on 2026-10-24" in cost.estimate_close(None)["basis"]
    run_close(conn, ModelPolicy(client=_PricedClient(input_tokens=100_000), budget_usd="6.00"))
    guess = cost.estimate_close(conn)
    assert Decimal(guess["usd"]) > 0 and "RUN-001" in guess["basis"]
    assert cost.estimate_score(conn, 3)["usd"] == str((Decimal(guess["usd"]) * 3).quantize(Decimal("0.01")))


def test_a_cap_outside_the_allowed_range_is_refused():
    for bad in ("0.10", "51"):
        with pytest.raises(ValueError, match="between"):
            cost.clamp_budget(bad)
    assert cost.clamp_budget("6") == Decimal("6.00")


def test_the_dashboard_shows_the_estimate_and_refuses_a_bad_cap(fresh):
    conn, path = fresh
    client = TestClient(create_app(path))
    estimate = client.get("/api/estimate", params={"runs": 4}).json()
    assert estimate["close"]["usd"] and estimate["close"]["basis"] and estimate["score"]["runs"] == 4
    assert estimate["default_budget"] == "6.00" and estimate["min_budget"] == "0.25"
    too_small = client.post("/api/jobs/close", json={"mode": "scripted", "budget_usd": 0.01})
    assert too_small.status_code == 400 and "between" in too_small.json()["detail"]
    assert client.post("/api/jobs/score", json={"set": "main", "runs": 3, "mode": "scripted",
                                                "budget_usd": 999}).status_code == 400


def test_the_close_command_shows_the_estimate_and_will_not_start_a_live_run_unasked(fresh, monkeypatch, capsys):
    from vouch.__main__ import main
    _, path = fresh
    monkeypatch.setattr(sys, "argv", ["vouch", "--db", str(path), "close"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as stopped:
        main()
    out = capsys.readouterr().out
    assert stopped.value.code == 2 and "estimated $" in out and "spending cap $6.00" in out and "pass --yes" in out
    assert conn_runs(path) == 0                                  # nothing started, nothing spent


def test_the_score_command_shows_the_estimate_and_will_not_start_a_live_score_unasked(monkeypatch, capsys):
    from vouch.evaluator.__main__ import main
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as stopped:
        main(["score", "--set", "main"])
    out = capsys.readouterr().out
    assert stopped.value.code == 2 and "3 full closes" in out and "estimated $" in out and "pass --yes" in out


def conn_runs(path) -> int:
    conn = connect(path)
    try:
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'agent_runs'").fetchone()
        return conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] if has else 0
    finally:
        conn.close()
