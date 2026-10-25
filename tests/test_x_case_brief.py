"""Cost control, Phase 2: the case brief. Code makes the lookups the model used to open every case with
(the case's state, the text of each rule it cites, the earlier rulings of its kind) and puts the full results in
front of it. Each lookup goes through the same tool, so it is audited (marked as prepared by Vouch) and joins the
evidence the claim check reads. Nothing is summarised, and nothing is invented when a lookup fails."""
import json
import shutil
from types import SimpleNamespace

import pytest

from vouch.agent import brief, runner
from vouch.agent.policy import ModelPolicy
from vouch.agent.tools import Toolbox
from vouch.controls import audit, gates
from vouch.controls.gates import VOUCH
from vouch.db import connect
from vouch.recon.run import reconcile


@pytest.fixture
def toolbox(db_path, tmp_path):
    path = tmp_path / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    runner.ensure(conn)
    for finding in reconcile(conn).cases():
        record = finding.to_record()
        gates.open_case(conn, VOUCH, record["finding_id"], record["period"], record["kind"], record["summary"], record)
    yield Toolbox(conn)
    conn.close()


def _case_with_rules(toolbox):
    return next(c for c in toolbox.cases.values() if len(c.get("rules") or []) >= 2)


def test_the_brief_holds_the_full_text_of_every_rule_the_case_cites(toolbox):
    case = _case_with_rules(toolbox)
    text = brief.build(toolbox, case)
    for rule_id in case["rules"]:
        rule = toolbox.src.rule(rule_id)                        # {"rule_id", "title", "text", ...} as loaded
        assert rule["title"] in text and rule["text"] in text   # the whole text, not a summary of it
        assert rule_id in text
    assert "<earlier_rulings_of_this_kind>" in text and "<case_state_now>" in text


def test_the_lookups_are_audited_as_prepared_by_vouch_and_join_the_evidence_the_claim_check_reads(toolbox):
    case = _case_with_rules(toolbox)
    before = len(toolbox.corpus)
    brief.build(toolbox, case)
    rows = [r for r in audit.trail(toolbox.conn, case["finding_id"]) if r["action"].startswith("tool:")]
    prepared = [r for r in rows if r["basis"].get("prefetched")]
    assert {r["action"] for r in prepared} == {"tool:get_case"}                  # only the case's own state targets it
    everything = [r for r in audit.trail(toolbox.conn) if r["basis"].get("prefetched")]
    assert {r["action"] for r in everything} == {"tool:get_policy", "tool:get_precedents", "tool:get_case"}
    assert all(r["actor"] == "Vouch" and r["outcome"] == "done" for r in everything)
    assert len(toolbox.corpus) == before + len(case["rules"]) + 2                # every result is evidence
    first_rule = toolbox.src.rule(case["rules"][0])
    assert any(first_rule["text"] in entry for entry in toolbox.corpus[before:])


def test_a_rule_that_cannot_be_read_is_said_so_never_invented(toolbox):
    case = dict(_case_with_rules(toolbox), rules=["NOPE-99"])
    text = brief.build(toolbox, case)
    assert "NOPE-99" in text and "could not be read" in text
    failed = [r for r in audit.trail(toolbox.conn) if r["basis"].get("input", {}).get("rule_id") == "NOPE-99"]
    assert failed and failed[0]["outcome"] == "failed"                            # logged, with its reason


class _OneTurnClient:
    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.first_prompt = None

    def create(self, **kwargs):
        self.first_prompt = self.first_prompt or kwargs["messages"][0]["content"][0]["text"]
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text="Done.")])


def test_the_model_gets_the_brief_in_its_first_message_and_is_told_not_to_look_it_up_again(toolbox):
    client = _OneTurnClient()
    case = _case_with_rules(toolbox)
    ModelPolicy(client=client).work(case, toolbox)
    prompt = client.first_prompt
    assert json.dumps(case, indent=1, default=str) in prompt                      # the case, unchanged
    assert "<policy_rules>" in prompt and case["rules"][0] in prompt
    assert "Do not look these up again" in prompt and "single turn" in prompt
