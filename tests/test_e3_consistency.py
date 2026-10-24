"""E3: a score rests on at least 3 runs, and a planted mistake counts as caught only if every run
agrees. The demo scenario (the main set's close) passes 5 runs in a row."""
import json

import pytest

from vouch.agent.policy import ScriptedPolicy
from vouch.evaluator import run
from vouch.evaluator.__main__ import main, parser


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    return run.prepare("main", tmp_path_factory.mktemp("e3"))


def flaky_factory():
    """Each new policy is the next run; even-numbered runs leave the duplicate payment unheld."""
    made = []

    class _Flaky(ScriptedPolicy):
        def __init__(self):
            super().__init__()
            made.append(self)
            self.run = len(made)

        def work(self, case, toolbox):
            if case["kind"] == "duplicate_payment" and self.run % 2 == 0:
                toolbox.call("record_decision", {"case_id": case["finding_id"], "status": "held",
                                                 "explanation": case["summary"]})
                return case["summary"]
            return super().work(case, toolbox)
    return _Flaky


def runs_of(prepared, factory, tmp_path, n):
    return [run.run_once(prepared, factory(), tmp_path, i) for i in range(1, n + 1)]


def test_a_mistake_caught_in_only_some_runs_does_not_count(prepared, tmp_path):
    report = run.make_report("main", "scripted", None, prepared.key, runs_of(prepared, flaky_factory(), tmp_path, 3))
    duplicate = next(p["plant_id"] for p in prepared.key["plants"] if p["expected_outcome"] == "reject_duplicate")
    consistency = report["metrics"]["consistency"]
    assert report["metrics"]["caught"]["missed"] == [duplicate] and not report["at_target"]["caught"]
    assert consistency["flaky"] == [duplicate] and consistency["plants"][duplicate]["caught_runs"] == 2
    assert consistency["handled_differently"] == [duplicate] and consistency["runs_at_target"] == [True, False, True]
    assert "flaky: " + duplicate + " (2/3)" in run.render(report)


def test_identical_runs_agree_and_are_handled_the_same_way(prepared, tmp_path):
    report = run.make_report("main", "scripted", None, prepared.key, runs_of(prepared, ScriptedPolicy, tmp_path, 3))
    consistency = report["metrics"]["consistency"]
    assert not consistency["flaky"] and not consistency["handled_differently"]
    assert all(c["caught_runs"] == 3 for c in consistency["plants"].values())


def test_the_command_scores_three_runs_by_default_and_refuses_fewer(capsys):
    assert parser().parse_args(["score"]).runs == run.MIN_RUNS == 3
    with pytest.raises(SystemExit) as exit_code:
        main(["score", "--scripted", "--runs", "2"])
    assert exit_code.value.code == 2 and "at least 3 runs" in capsys.readouterr().out


def test_a_streak_broken_by_one_run_fails_the_demo():
    assert run.longest_streak([True, True, False, True, True]) == 2
    report = {"set": "main", "runs": 5, "metrics": {"consistency": {"runs_at_target": [True, True, False, True, True]}}}
    assert run.demo_verdict(report) == {"runs": 5, "longest_in_a_row": 2, "needed": 5, "passed": False}
    clean = {**report, "set": "clean", "metrics": {"consistency": {"runs_at_target": [True] * 5}}}
    assert run.demo_verdict(clean)["passed"] is False                  # the demo scenario is the main set


def test_the_demo_scenario_passes_five_runs_in_a_row(tmp_path, capsys):
    """Scripted mode, measured. The live model's five runs need an API key and are reported separately."""
    with pytest.raises(SystemExit) as exit_code:
        main(["score", "--scripted", "--demo", "--results", str(tmp_path)])
    out = capsys.readouterr().out
    assert exit_code.value.code == 0 and "Demo scenario      PASSED: 5 run(s) in a row" in out
    report = json.loads(next(tmp_path.glob("main-*.json")).read_text(encoding="utf-8"))
    assert report["runs"] == 5 and report["demo"] == {"runs": 5, "longest_in_a_row": 5, "needed": 5, "passed": True}
