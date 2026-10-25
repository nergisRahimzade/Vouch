"""Additional agent-authored regressions, never presented as an independent holdout."""
from pathlib import Path
import pytest
from vouch.evaluator.answer_key import load_cases
from vouch.evaluator.run import evaluate_one

CASES = load_cases(Path(__file__).resolve().parents[1] / 'cases' / 'supplemental')

@pytest.mark.parametrize('scenario,expected', CASES, ids=[s.id for s,_ in CASES])
def test_supplemental_exact_amounts_and_controls(scenario, expected):
    assert not hasattr(scenario,'expect')
    row = evaluate_one(scenario,expected,scripted=True)
    assert row['score']['passed'],row['score']
    assert row['score']['amounts_correct']
    assert not row['score']['safety_violations']
