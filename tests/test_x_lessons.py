"""Learning loop, Phase 4: after a live close, code turns the agent's own dead ends into lessons about a kind of
case, with every identifier and name taken out. Nothing changes until the controller approves a lesson; approved
lessons reach every later close through the case brief, and every score names the lessons it ran with. Lessons are
learned only from the main set, and never from the scripted playbook."""
import json
import shutil
from types import SimpleNamespace

import pytest

from vouch.agent import brief, lessons
from vouch.agent.policy import ModelPolicy, ScriptedPolicy
from vouch.agent.runner import run_close
from vouch.agent.tools import Toolbox
from vouch.controls import audit, gates
from vouch.controls.gates import VOUCH
from vouch.db import connect
from vouch.db.periods import directory_actor


class _WrongHoldClient:
    """Each case: first a hold on something that is not part of the case (refused by the tool), then stop."""
    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.turn = {}

    def create(self, **kwargs):
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if not kwargs.get("tools"):                                     # the report
            return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[SimpleNamespace(type="text", text="")])
        first = kwargs["messages"][0]["content"][0]["text"]
        case_id = json.loads(first.split("<case>", 1)[1].split("</case>", 1)[0])["finding_id"]
        if self.turn.get(case_id):
            return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[SimpleNamespace(type="text", text="ok")])
        self.turn[case_id] = 1
        call = SimpleNamespace(type="tool_use", name="hold", id=f"t-{case_id}", input={
            "case_id": case_id, "target_kind": "payment", "target_ref": "PAY-2026-09-US-0999",
            "reason": "Hold for Amara Okafor to review"})
        return SimpleNamespace(stop_reason="tool_use", usage=usage, content=[call])


@pytest.fixture
def lived(db_path, tmp_path, monkeypatch):
    """A close by a (stand-in) live model that made the same dead end on every case."""
    monkeypatch.setattr(lessons, "APPROVED_FILE", tmp_path / "approved.json")
    path = tmp_path / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    run_close(conn, ModelPolicy(client=_WrongHoldClient(), parallel_cases=1))
    yield conn
    conn.close()


def test_dead_ends_become_proposed_lessons_about_kinds_with_no_ids_or_names(lived):
    proposed = lessons.propose(lived)
    kinds = {r[0] for r in lived.execute("SELECT kind FROM cases")}
    assert proposed and {p["kind"] for p in proposed} <= kinds
    for p in proposed:
        assert "PAY-2026-09-US-0999" not in p["text"] and "F-" not in p["text"]
        assert "is not part of case" in p["text"] and "<id>" in p["text"]
        assert p["evidence"]                                              # the audit rows it came from
    assert lessons.propose(lived) == []                                   # proposing again adds nothing
    logged = [r for r in audit.trail(lived) if r["action"] == "propose_lesson"]
    assert len(logged) == len(proposed) and all(r["outcome"] == "requested" for r in logged)


def test_names_and_account_numbers_are_taken_out_and_rule_ids_kept():
    text = lessons.scrub("Amara Okafor asked to pay DE65996892983857086879 under FIN-07 for NL-00238 and "
                         "PAY-2026-09-DE-0033 (rule PAY-04)", ["Amara Okafor"])
    assert "Amara" not in text and "DE6599" not in text and "NL-00238" not in text and "PAY-2026" not in text
    assert "FIN-07" in text and "PAY-04" in text


def test_only_the_controller_approves_and_vouch_is_refused_and_logged(lived):
    lesson = lessons.propose(lived)[0]
    with pytest.raises(gates.Refused, match="only the controller"):
        lessons.decide(lived, VOUCH, lesson["lesson_id"], "approve", "looks right")
    refused = [r for r in audit.trail(lived, lesson["lesson_id"]) if r["outcome"] == "refused"]
    assert refused and refused[0]["actor"] == "Vouch"
    with pytest.raises(gates.Refused, match="needs a note"):
        lessons.decide(lived, directory_actor(lived, "controller"), lesson["lesson_id"], "approve", " ")
    assert lessons.load_all() == []                                       # nothing reaches a close yet


def test_an_approved_lesson_reaches_the_next_brief_and_the_score_label(lived):
    lesson = lessons.propose(lived)[0]
    before = lessons.fingerprint()
    done = lessons.decide(lived, directory_actor(lived, "controller"), lesson["lesson_id"], "approve",
                          "Hold only what the case is about.")
    assert done["status"] == "approved" and lessons.for_kind(lesson["kind"]) == [lesson["text"]]
    assert lessons.fingerprint() != before and lessons.fingerprint()["count"] == 1
    toolbox = Toolbox(lived)
    case = next(c for c in toolbox.cases.values() if c["kind"] == lesson["kind"])
    other = next(c for c in toolbox.cases.values() if c["kind"] != lesson["kind"])
    assert lesson["text"] in brief.build(toolbox, case)
    assert lesson["text"] not in brief.build(toolbox, other)              # only for its own kind
    with pytest.raises(gates.Refused, match="already approved"):
        lessons.decide(lived, directory_actor(lived, "controller"), lesson["lesson_id"], "reject", "changed my mind")


def test_a_rejected_lesson_never_reaches_a_close(lived):
    lesson = lessons.propose(lived)[0]
    lessons.decide(lived, directory_actor(lived, "controller"), lesson["lesson_id"], "reject", "Too narrow.")
    assert lessons.for_kind(lesson["kind"]) == []


def test_no_lessons_from_another_set_or_from_the_scripted_playbook(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(lessons, "APPROVED_FILE", tmp_path / "approved.json")
    path = tmp_path / "s.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    try:
        run_close(conn, ScriptedPolicy())
        with pytest.raises(gates.Refused, match="live close"):
            lessons.propose(conn)
        lessons.set_world(conn, "clean")
        with pytest.raises(gates.Refused, match="only from the main set"):
            lessons.propose(conn)
    finally:
        conn.close()
