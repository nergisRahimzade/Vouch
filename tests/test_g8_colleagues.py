"""G8: a model can play the approvers and an auditor in the demo, with a scripted fallback; the demo
runs with either. Colleagues act only through the controls, as the people they play."""
import json
import shutil
from types import SimpleNamespace

import pytest

from vouch.agent.colleagues import ModelColleagues, ScriptedColleagues, auditor_review, respond
from vouch.agent.policy import ModelPolicy, ScriptedPolicy
from vouch.agent.runner import run_close
from vouch.controls import audit
from vouch.db import connect


@pytest.fixture
def closed(db_path, tmp_path):
    path = tmp_path / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    run_close(conn, ScriptedPolicy())
    yield conn
    conn.close()


class _ColleagueClient:
    """Stands in for the API with structured answers in the shape output_config.format returns."""
    def __init__(self, fail=False):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.fail, self.calls = fail, 0

    def create(self, **kwargs):
        self.calls += 1
        usage = SimpleNamespace(input_tokens=50, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        if self.fail:
            return SimpleNamespace(stop_reason="refusal", content=[], usage=usage)
        schema = kwargs["output_config"]["format"]["schema"]["properties"]
        if "answer" in schema:
            body = {"answer": "no", "note": "I cannot confirm that from what I have.", "callback_confirmed": False}
        elif "decision" in schema:
            body = {"decision": "approve", "note": "Reviewed the sources; approved."}
        else:
            body = {"question": "What evidence did you rely on, and who confirmed it?"}
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(body))])


def _demo(conn, colleagues, policy):
    round_one = respond(conn, colleagues)
    review = auditor_review(conn, colleagues, policy)
    return round_one, review


def test_the_demo_runs_with_scripted_colleagues(closed):
    result, review = _demo(closed, ScriptedColleagues(), ScriptedPolicy())
    answers = {a["answer"] for a in result["answered"]}
    assert {"yes", "no", "partial"} <= answers
    assert result["posted"] and all(p.startswith("VCH-") for p in result["posted"])
    posters = {r[0] for r in closed.execute("SELECT actor FROM audit_log WHERE action = 'post_entry'")}
    assert posters and "Vouch" not in posters
    rejected = closed.execute("SELECT COUNT(*) FROM rulings WHERE source LIKE 'approval:%'").fetchone()[0]
    assert rejected == 1                                                # the self-approved accrual became a precedent
    held = [r for r in result["refused"] if "payment" in r]
    assert held and all(r["rule"] in ("PAY-06", "FIN-06") for r in held)   # held payments were not released
    assert result["released"] > 200
    assert review and all(r["ok"] for r in review)
    assert audit.verify(closed).ok


def test_the_demo_runs_with_model_colleagues(closed):
    client = _ColleagueClient()
    policy = ModelPolicy(client=client)
    result, review = _demo(closed, ModelColleagues(policy, closed), ScriptedPolicy())
    assert client.calls >= len(result["answered"]) + len(result["decided"])
    assert result["answered"] and all(a["answer"] == "no" for a in result["answered"])
    assert result["posted"] and review
    assert all(r["question"] == "What evidence did you rely on, and who confirmed it?" for r in review)


def test_a_question_cannot_promise_a_step_the_answerer_cannot_take(closed):
    from vouch.agent import questions
    case_id = closed.execute("SELECT case_id FROM cases WHERE kind = 'entity_differs_from_hr'").fetchone()[0]
    with pytest.raises(questions.BadQuestion, match="cannot release holds"):
        questions.ask(closed, case_id, "department_head", "Olu Adeyemi", "Did they move?",
                      ["hr_census", "payroll_register"], "keep_hold", "release_hold", "ask_follow_up")


def test_a_failing_model_colleague_falls_back_to_the_script(closed):
    policy = ModelPolicy(client=_ColleagueClient(fail=True))
    result, _ = _demo(closed, ModelColleagues(policy, closed), ScriptedPolicy())
    fallbacks = closed.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'colleague_fallback'").fetchone()[0]
    assert fallbacks >= len(result["answered"]) and {"yes", "no", "partial"} <= {a["answer"] for a in result["answered"]}
