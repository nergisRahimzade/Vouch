"""G3: a question promises only next steps that can be carried out. Promising a correcting entry for a case
that no entry can settle is refused when asked; a promise made anyway ends in a held case with a plain
reason, never a crash."""
import shutil

import pytest

from vouch.agent import questions
from vouch.agent.policy import ScriptedPolicy
from vouch.agent.runner import run_close
from vouch.agent.tools import Toolbox
from vouch.db import connect
from vouch.db.periods import Actor


@pytest.fixture
def closed(db_path, tmp_path):
    path = tmp_path / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    run_close(conn, ScriptedPolicy())
    yield conn
    conn.close()


def _undraftable(conn) -> str:
    return conn.execute("SELECT case_id FROM cases WHERE kind = 'entity_differs_from_hr'").fetchone()[0]


def test_a_question_cannot_promise_a_correction_no_entry_can_make(closed):
    toolbox = Toolbox(closed)
    case_id = _undraftable(closed)
    before = closed.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    result = toolbox.call("ask", {"case_id": case_id, "to_role": "department_head", "fact_needed": "Did they move?",
                                  "sources_disagree": ["hr_census", "payroll_register"], "if_yes": "draft_correction",
                                  "if_no": "keep_hold", "if_partial": "ask_follow_up"})
    assert not result["ok"] and "cannot promise draft_correction" in result["error"]
    assert closed.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == before


def test_a_promise_that_cannot_be_kept_holds_the_case_with_a_plain_reason(closed):
    toolbox = Toolbox(closed)
    case_id = _undraftable(closed)
    head = toolbox.src.person_with_role("department_head", toolbox.cases[case_id]["subject"].get("department"))
    question = questions.ask(closed, case_id, "department_head", head, "Did the transfer happen?",
                             ["hr_census", "payroll_register"], "draft_correction", "keep_hold", "keep_hold")
    result = questions.answer(closed, question, Actor(head, "department_head"), "yes", "Yes, it happened.",
                              draft=toolbox.draft_for)
    assert result["next_step"] == "keep_hold"
    status, note = closed.execute("SELECT status, note FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    assert status == "held" and "cannot be drafted" in note
    assert not closed.execute("SELECT 1 FROM journal_entries WHERE entry_id = ?", (f"VCH-{case_id}",)).fetchone()
