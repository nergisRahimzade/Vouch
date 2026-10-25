"""E1: scorer measures actual decisions, requires repeats, and keeps answers outside the agent."""
from decimal import Decimal
import pytest
from vouch.agent.runner import run_scenario
from vouch.evaluator.answer_key import load_cases
from vouch.evaluator.match import compare
from vouch.evaluator.run import evaluate
from vouch.evaluator.score import score_decisions
from vouch.world.scenario import parse


@pytest.mark.parametrize("case_id",["P01","P02","P03","P04","P05","P06","P07","P08","P09","P10","P11","C01","C02","C03","C04"])
def test_public_case_matches_the_independent_spec_answers(public_case,case_id):
    conn,s=public_case(case_id)
    expected=next(e for scenario,e in load_cases() if scenario.id==case_id)
    result=run_scenario(conn,s)
    score=score_decisions(conn,result["decisions"],expected)
    assert score["passed"],score


def test_changed_amount_and_forbidden_action_fail():
    r=compare({"deductible":"525.02"},{"deductible":"525.00","must_not":["change_election"]},["change_election"])
    assert not r["passed"] and not r["amounts_correct"]
    assert compare({"deductible":"525.01"},{"deductible":"525.00"})["passed"]


def test_missing_decision_never_counts_as_success():
    assert not compare({}, {"decision":"classify_and_book"})["passed"]
    with pytest.raises(ValueError): evaluate(scripted=True,runs=1,save=False)


def test_answer_block_is_not_part_of_agent_scenario():
    s=parse({"id":"example","as_of":"2026-10-01","expect":{"secret":"not visible"}})
    assert not hasattr(s,"expect") and "secret" not in repr(s)


def test_unconfigured_holdout_returns_no_score(monkeypatch):
    monkeypatch.delenv("VOUCH_HOLDOUT_PATH",raising=False)
    r=evaluate(holdout=True,scripted=True,runs=3,save=False)
    assert r["status"]=="not_scored" and "details" not in r


def test_wrong_correction_amount_is_not_counted_as_amount_accurate():
    r=compare({"corrections":[{"type":"vat_reversal","amount":"20.02"}]},
              {"corrections":[{"type":"vat_reversal","amount":"20.00"}]})
    assert not r["passed"] and not r["amounts_correct"]
