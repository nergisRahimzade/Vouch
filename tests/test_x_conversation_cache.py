"""Cost control, Phase 1: each turn marks the end of the conversation so far as cacheable, so the API bills what
it has already seen at the cached rate. The words sent must not change: only the marker moves."""
import copy
import json
import shutil
from types import SimpleNamespace

import pytest

from vouch.agent import runner
from vouch.agent.policy import ModelPolicy, _cache_conversation
from vouch.agent.tools import Toolbox
from vouch.controls import gates
from vouch.controls.gates import VOUCH
from vouch.db import connect
from vouch.recon.run import reconcile


def _markers(messages):
    return [(i, j) for i, m in enumerate(messages) if m["role"] == "user"
            for j, block in enumerate(m["content"]) if "cache_control" in block]


def test_only_the_newest_user_message_is_marked_and_older_markers_are_cleared():
    messages = [{"role": "user", "content": [{"type": "text", "text": "case"}]}]
    _cache_conversation(messages)
    assert _markers(messages) == [(0, 0)]
    messages += [{"role": "assistant", "content": []},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "x"},
                                              {"type": "tool_result", "tool_use_id": "b", "content": "y"}]}]
    _cache_conversation(messages)
    assert _markers(messages) == [(2, 1)]                       # the last block of the newest message, nothing else
    _cache_conversation(messages)                                # marking twice changes nothing
    assert _markers(messages) == [(2, 1)]


class _TwoTurnClient:
    """First turn asks for one tool; the second finishes. Records what it was sent each turn."""
    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.sent = []

    def create(self, **kwargs):
        self.sent.append({"system": copy.deepcopy(kwargs["system"]), "messages": copy.deepcopy(kwargs["messages"])})
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if len(self.sent) == 1:
            call = SimpleNamespace(type="tool_use", name="list_cases", input={}, id="toolu_1")
            return SimpleNamespace(stop_reason="tool_use", usage=usage, content=[call])
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text="Done.")])


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


def test_every_turn_sends_the_conversation_marked_once_and_the_instructions_marked_once(toolbox):
    client = _TwoTurnClient()
    policy = ModelPolicy(client=client)
    case = next(iter(toolbox.cases.values()))
    assert policy.work(case, toolbox) == "Done."
    first, second = client.sent
    assert _markers(first["messages"]) == [(0, 0)]
    assert _markers(second["messages"]) == [(2, 0)]            # moved to the tool result; the case message is clear
    for sent in (first, second):
        assert [b for b in sent["system"] if "cache_control" in b]            # the instructions stay cached
        assert len(_markers(sent["messages"])) + len(sent["system"]) <= 4     # the API's limit on markers


def test_the_words_sent_are_the_same_with_or_without_the_marker(toolbox):
    client = _TwoTurnClient()
    case = next(iter(toolbox.cases.values()))
    ModelPolicy(client=client).work(case, toolbox)
    second = client.sent[1]["messages"]

    def words(messages):
        out = copy.deepcopy(messages)
        for m in out:
            if m["role"] == "user":
                for block in m["content"]:
                    block.pop("cache_control", None)
        return json.dumps([m["content"] if m["role"] == "user" else "assistant" for m in out], default=str)
    assert json.loads(words(second))[0][0]["text"].startswith("Work this case.")
    assert json.loads(words(second))[2][0]["type"] == "tool_result"
    assert "cache_control" not in words(second)
