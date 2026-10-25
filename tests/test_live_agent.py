"""Live Claude integration tests. These make billed API calls; credentials never leave .env.

Run only these: python -m pytest -m live
Run local deterministic checks while implementing: python -m pytest -m "not live"
The ordinary full suite includes these tests; missing credentials fail explicitly, with no silent skip.
"""
from pathlib import Path
import pytest

from vouch.agent.policy import MODEL, api_key_available
from vouch.evaluator.run import evaluate

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_public_report():
    if not api_key_available():
        pytest.fail("Live tests require ANTHROPIC_API_KEY in the environment or project .env; no live run was performed.")
    report = evaluate(scripted=False, runs=3, concurrency=6)
    assert report["status"] == "scored", report
    assert report["mode"] == "model" and report["model"] == MODEL
    return report


@pytest.mark.parametrize("case_id", ["P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09", "P10", "P11", "C01", "C02", "C03", "C04"])
def test_live_public_case_matches_spec_on_every_run(live_public_report, case_id):
    report = live_public_report
    runs = [next(row for row in batch if row["case_id"] == case_id) for batch in report["details"]]
    assert len(runs) == 3
    for row in runs:
        assert row["score"]["passed"], row["score"]
        assert row["score"]["books_balance"]
        assert not row["score"]["safety_violations"]
        assert row["run"]["metrics"]["llm_calls"] > 0
        assert not any(d.get("failure") for d in row["run"]["decisions"])


def test_live_prompt_cache_and_usage_are_measured(live_public_report):
    metrics = [row["run"]["metrics"] for batch in live_public_report["details"] for row in batch]
    assert all(m["input_tokens"] > 0 and m["output_tokens"] > 0 for m in metrics)
    assert all(m["cost_usd"] is not None for m in metrics)
    assert sum(m["cache_read_input_tokens"] for m in metrics) > 0
    assert live_public_report["runs"] == 3 and live_public_report["date"]


def test_live_p11_passes_five_consecutive_runs():
    if not api_key_available():
        pytest.fail("Live P11 rehearsal requires ANTHROPIC_API_KEY; no live run was performed.")
    report = evaluate(scripted=False, runs=5, concurrency=1, case_ids=["P11"])
    assert report["mode"] == "model" and report["model"] == MODEL
    assert report["runs"] == 5 and report["cases"] == 1
    assert len(report["details"]) == 5
    for batch in report["details"]:
        assert batch[0]["case_id"] == "P11"
        assert batch[0]["score"]["passed"], batch[0]["score"]
        assert not batch[0]["score"]["safety_violations"]
