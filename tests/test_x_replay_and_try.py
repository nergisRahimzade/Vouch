"""Selective validation, Phase 5: a live close can be recorded and replayed for free (labelled as a replay, $0,
same decisions); a replay that is asked for something it never recorded stops plainly; and one kind of case can
be tried on a throwaway copy without touching the real database."""
import shutil
from types import SimpleNamespace

from vouch.agent.policy import ModelPolicy, ScriptedPolicy, recording_policy, replay_policy
from vouch.agent.runner import run_close
from vouch.agent.trycase import try_case
from vouch.db import connect


class _Reply(SimpleNamespace):
    def model_dump(self, mode="json"):
        def plain(v):
            if isinstance(v, SimpleNamespace):
                return {k: plain(x) for k, x in vars(v).items()}
            if isinstance(v, list):
                return [plain(x) for x in v]
            return v
        return plain(self)


class _HoldThenStop:
    """A stand-in live model: holds the case's subject on the first turn, then stops. Costs known tokens."""
    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        done = _Reply(stop_reason="end_turn", usage=usage, model="claude-opus-5-5",
                      content=[SimpleNamespace(type="text", text="Recorded decision.")])
        if not kwargs.get("tools") or any(m["role"] == "assistant" for m in kwargs["messages"]):
            return done
        import json
        text = kwargs["messages"][0]["content"][0]["text"]
        case = json.loads(text.split("<case>", 1)[1].split("</case>", 1)[0])
        ref = case["subject"].get("employee_id")
        if not ref:
            return done
        call = SimpleNamespace(type="tool_use", name="hold", id=f"t-{case['finding_id']}",
                               input={"case_id": case["finding_id"], "target_kind": "person", "target_ref": ref,
                                      "reason": "held while it is checked"})
        return _Reply(stop_reason="tool_use", usage=usage, model="claude-opus-5-5", content=[call])


def _copy(db_path, tmp_path, name):
    path = tmp_path / name
    shutil.copyfile(db_path, path)
    return connect(path)


def _holds(conn):
    return sorted(tuple(r) for r in conn.execute("SELECT case_id, target_kind, target_ref FROM holds"))


def test_a_recorded_close_replays_for_free_with_the_same_decisions(db_path, tmp_path):
    rec = tmp_path / "run.jsonl"
    live = _copy(db_path, tmp_path, "live.db")
    policy = recording_policy(rec, client=_HoldThenStop(), parallel_cases=4)
    first = run_close(live, policy)
    again = _copy(db_path, tmp_path, "replay.db")
    second = run_close(again, replay_policy(rec, parallel_cases=4))
    try:
        assert rec.read_text(encoding="utf-8").count("\n") > first["cases"]
        assert _holds(live) and _holds(live) == _holds(again)
        run = again.execute("SELECT mode, model, cost_usd FROM agent_runs").fetchone()
        assert run["mode"] == "replay" and run["model"] == "replay of claude-opus-5-5"
        assert second["cost_usd"] == "0.0000" and first["cost_usd"] != "0.0000"
    finally:
        live.close()
        again.close()


def test_a_replay_asked_for_something_it_never_recorded_stops_plainly(db_path, tmp_path):
    rec = tmp_path / "empty.jsonl"
    rec.write_text('{"key": "call:nothing", "turn": 0, "response": {"model": "claude-opus-5-5"}}\n', encoding="utf-8")
    conn = _copy(db_path, tmp_path, "r.db")
    try:
        result = run_close(conn, replay_policy(rec, parallel_cases=1))
        assert result["failures"] and all("has no reply" in r for r in result["failures"].values())
    finally:
        conn.close()


def test_try_case_works_one_kind_on_a_copy_and_leaves_the_real_database_alone(db_path, tmp_path):
    real = tmp_path / "real.db"
    shutil.copyfile(db_path, real)
    before = real.read_bytes()
    result = try_case(real, "paid_after_exit", 1, policy=ScriptedPolicy())
    assert result["kind"] == "paid_after_exit" and result["status"] in ("held", "waiting")
    assert result["mode"] == "scripted" and (result["holds"] or result["questions"] or result["draft"])
    assert real.read_bytes() == before                          # not a byte written
    missing = try_case(real, "no_such_kind", 1, policy=ScriptedPolicy())
    assert "no no_such_kind case" in missing["error"]
