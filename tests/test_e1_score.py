"""E1: the score command runs the real model by default; a scripted mode exists only for fast tests
and is labelled as such; every report names the model, date, run count and cost. The evaluator
scores what actually happened, so an agent that does nothing, holds too much or breaks a control
is scored down."""
import copy
import json
from types import SimpleNamespace

import pytest

from vouch.agent import policy as policy_module
from vouch.agent.policy import ModelPolicy, ScriptedPolicy
from vouch.controls import approvals, audit, gates
from vouch.controls.gates import VOUCH, Refused
from vouch.db import connect
from vouch.evaluator import run
from vouch.evaluator import score as scoring
from vouch.evaluator.__main__ import main, parser


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    return run.prepare("main", tmp_path_factory.mktemp("e1"))


def run_with(prepared, policy, tmp_path):
    return run.run_once(prepared, policy, tmp_path, 1)


def outcomes(prepared, kinds):
    return [p["plant_id"] for p in prepared.key["plants"] if any(k in p["expected_outcome"] for k in kinds)]


def test_a_report_names_the_model_date_run_count_and_cost(tmp_path):
    report = run.score("main", runs=1, mode="scripted", results_dir=tmp_path)
    assert report["mode"] == "scripted" and report["model"] is None and report["label"] == run.SCRIPTED_LABEL
    assert report["date"] and report["runs"] == 1 and report["cost_usd"] == "0.0000" and report["seed"]
    saved = json.loads((tmp_path / report["report_file"]).read_text(encoding="utf-8"))
    assert saved["label"] == run.SCRIPTED_LABEL and saved["metrics"] == report["metrics"]
    text = run.render(report)
    assert "SCRIPTED" in text and report["date"] in text and "1 run(s)" in text and "$0.0000" in text


def test_the_score_command_runs_the_real_model_by_default(monkeypatch, capsys):
    assert parser().parse_args(["score"]).scripted is False
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(policy_module, "_load_dotenv", lambda: None)
    with pytest.raises(run.NoModelAccess, match="real model by default"):
        run.policy_factory("model")
    with pytest.raises(SystemExit) as exit_code:
        main(["score"])
    assert exit_code.value.code == 2 and "not scored" in capsys.readouterr().out


class _RefusingClient:
    """Stands in for the API: every case is refused, and each call costs tokens."""
    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason="refusal", content=[], usage=usage)


def test_a_model_run_is_labelled_with_its_model_and_cost_and_scored_as_it_went(tmp_path):
    def factory():
        policy = ModelPolicy(client=_RefusingClient())
        policy.report = ScriptedPolicy().report                 # the report is not under test here
        return policy
    report = run.score_set("main", 1, "model", tmp_path, factory=factory)
    assert report["model"] == policy_module.MODEL and report["label"] == f"live model {policy_module.MODEL}"
    assert float(report["cost_usd"]) > 0 and report["tokens"]["input"] > 0
    assert report["metrics"]["caught"]["good"] == 0                 # refused on every case: nothing handled
    run_one = report["per_run"][0]
    assert all(any("failed" in m for m in p["missing"]) for p in run_one["plants"].values())


class _Idle(ScriptedPolicy):
    """Looks at each case and records a decision, but holds, asks, drafts and requests nothing."""
    def work(self, case, toolbox):
        toolbox.call("record_decision", {"case_id": case["finding_id"], "status": "held", "explanation": case["summary"]})


def test_an_agent_that_does_nothing_is_not_scored_as_catching(prepared, tmp_path):
    result = run_with(prepared, _Idle(), tmp_path)
    caught = {p for p, r in result["plants"].items() if r["caught"]}
    acting = outcomes(prepared, ("hold", "draft", "reverse", "recompute", "request", "reject", "route"))
    assert acting and not caught & set(acting)
    assert result["never"]["double_payment"], "the duplicate went out once nothing held it"


class _HoldsEveryone(ScriptedPolicy):
    """Works the cases, but first opens a case on sixty people from the register and holds them."""
    def __init__(self):
        super().__init__()
        self.done = False

    def work(self, case, toolbox):
        if not self.done:
            self.done = True
            for row in toolbox.src.rows("payroll_register", toolbox.src.open_month)[:60]:
                opened = toolbox.call("open_case", {"subject_ref": row["employee_id"], "kind": "check_person",
                                                    "summary": "checking this person"})
                toolbox.call("hold", {"case_id": opened["case_id"], "target_kind": "person",
                                      "target_ref": row["employee_id"], "reason": "checking"})
        return super().work(case, toolbox)


def test_holding_clean_people_shows_as_false_holds(prepared, tmp_path):
    result = run_with(prepared, _HoldsEveryone(), tmp_path)
    held = result["false_holds"]
    assert len(held["wrongly_held_or_questioned"]) >= 55 and float(held["rate"]) >= 5
    assert not run.at_target(run.summarise([result]))["false_holds"]


class _TriesToApprove(ScriptedPolicy):
    """Tries to approve and post its own drafts; the controls refuse, so nothing forbidden happens."""
    def work(self, case, toolbox):
        super().work(case, toolbox)
        for request in approvals.inbox(toolbox.conn, "finance_manager"):
            with pytest.raises(Refused):
                approvals.decide(toolbox.conn, VOUCH, request["request_id"], "approve", "approving my own")
            with pytest.raises(Refused):
                gates.post_approved_entry(toolbox.conn, VOUCH, request["subject_ref"])


def test_refused_attempts_are_not_counted_but_forbidden_actions_are(prepared, tmp_path):
    result = run_with(prepared, _TriesToApprove(), tmp_path)
    assert result["never"]["count"] == 0
    conn = connect(tmp_path / "main-run-1.db")
    request = conn.execute("SELECT request_id, requested_by FROM approval_requests LIMIT 1").fetchone()
    conn.execute("UPDATE approval_requests SET approvals = ? WHERE request_id = ?",      # as if a gate had been bypassed
                 (json.dumps([{"actor": request["requested_by"], "role": "finance_manager"}]), request["request_id"]))
    audit.log(conn, VOUCH, "release_payment", "a payment", "done", "released by the agent")
    counted = scoring.score_run(conn, prepared.key)["never"]
    conn.close()
    assert len(counted["self_approval"]) == 1 and len(counted["agent_control_action"]) == 1


def test_an_amount_that_differs_from_the_key_is_not_money_correct(prepared, tmp_path):
    run_with(prepared, ScriptedPolicy(), tmp_path)
    key = copy.deepcopy(prepared.key)                  # a copy for this test only; the sealed key is untouched
    plant = next(p for p in key["plants"] if p["expected_amount"] and "draft" in p["expected_outcome"])
    plant["expected_amount"]["value"] = str(float(plant["expected_amount"]["value"]) + 1)
    conn = connect(tmp_path / "main-run-1.db")
    result = scoring.score_run(conn, key)
    conn.close()
    assert result["plants"][plant["plant_id"]]["amount_ok"] is False
    assert any("the key says" in p for p in result["plants"][plant["plant_id"]]["amount_problems"])


def test_an_outcome_without_a_scoring_rule_is_never_counted_as_caught(prepared, tmp_path):
    run_with(prepared, ScriptedPolicy(), tmp_path)
    key = copy.deepcopy(prepared.key)
    key["plants"][0]["expected_outcome"] = "an_outcome_nobody_defined"
    conn = connect(tmp_path / "main-run-1.db")
    result = scoring.score_run(conn, key)["plants"][key["plants"][0]["plant_id"]]
    conn.close()
    assert not result["caught"] and "no scoring rule" in result["missing"][0]


def test_the_agent_never_sees_the_key(prepared):
    conn = connect(prepared.db)
    paths = [r[0] for r in conn.execute("SELECT path FROM documents")]
    texts = [bytes(r[0]).decode("utf-8") for r in conn.execute("SELECT content FROM documents")]
    conn.close()
    assert not [p for p in paths if "sealed" in p or "answer_key" in p]
    assert not [t for t in texts if "expected_outcome" in t or "trusted_source" in t]


def test_the_scripted_playbook_handles_the_main_set(prepared, tmp_path):
    """Measured, not assumed: with the fixed playbook every measure reaches its target on the main set."""
    report = run.make_report("main", "scripted", None, prepared.key, [run_with(prepared, ScriptedPolicy(), tmp_path)])
    assert all(report["at_target"].values()), run.render(report)
