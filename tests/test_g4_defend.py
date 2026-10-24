"""G4: asked "why did you do this?", Vouch answers from its recorded evidence (data, rule, document,
person, date); the answer cites only what is in the audit trail."""
import json
import shutil

import pytest

from vouch.agent.defend import evidence_for, why
from vouch.agent.policy import ScriptedPolicy
from vouch.agent.runner import run_close
from vouch.db import connect


@pytest.fixture(scope="module")
def closed(db_path, tmp_path_factory):
    path = tmp_path_factory.mktemp("defend") / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    run_close(conn, ScriptedPolicy())
    yield conn
    conn.close()


def case_id(conn, kind):
    return conn.execute("SELECT case_id FROM cases WHERE kind = ?", (kind,)).fetchone()[0]


@pytest.mark.parametrize("kind", ["bank_details_differ_from_hr", "contribution_rate_differs_from_policy",
                                  "payments_not_cleared"])
def test_the_answer_comes_from_the_audit_trail(closed, kind):
    cid = case_id(closed, kind)
    result = why(closed, cid, "Why did you do this?", ScriptedPolicy())
    assert result["ok"] and result["rows"], result["problems"]
    finding = next(r["basis"]["finding"] for r in evidence_for(closed, cid) if r["action"] == "open_case")
    answer = result["answer"]
    assert finding["trusted_source"] in answer and all(rule in answer for rule in finding["rules"])
    assert finding["amount"] in answer
    assert any(e["says"] in answer for e in finding["evidence"])


def test_the_answer_names_who_was_asked_and_when(closed):
    cid = case_id(closed, "bank_details_differ_from_hr")
    rows = evidence_for(closed, cid)
    ask = next(r for r in rows if r["action"] == "ask")
    answer = why(closed, cid, "Who did you ask?", ScriptedPolicy())["answer"]
    assert ask["basis"]["to"] in answer and ask["at"] in answer
    assert "RUL-0001" in answer                                   # the precedent it followed


class _Inventor(ScriptedPolicy):
    """Answers with a rule and an amount that are nowhere in the trail."""
    def defend(self, question, rows, problems=None):
        return "I held it under FIN-99 because 12,345.67 was missing on 2001-01-01."


def test_an_answer_citing_anything_outside_the_trail_is_rejected(closed):
    cid = case_id(closed, "duplicate_payment")
    result = why(closed, cid, "Why?", _Inventor())
    assert not result["ok"] and result["answer"] == ""
    assert {"rule FIN-99 does not appear in any tool result", "amount 12,345.67 does not appear in any tool result",
            "date 2001-01-01 does not appear in any tool result"} <= set(result["problems"])
    last = closed.execute("SELECT outcome, basis FROM audit_log WHERE action = 'defend' ORDER BY seq DESC LIMIT 1").fetchone()
    assert last[0] == "refused" and json.loads(last[1])["problems"]


def test_a_case_with_no_record_says_so(closed):
    result = why(closed, "F-000000000000", "Why?", ScriptedPolicy())
    assert result["ok"] and "no record" in result["answer"]
