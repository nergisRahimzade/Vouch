"""G6: timeout, refusal, or a tool failure holds the actual subject without posting."""
import pytest
from vouch.agent.policy import ScriptedPolicy,PolicyFailed
from vouch.agent.runner import run_scenario
from vouch.agent.tools import Toolbox


@pytest.mark.parametrize("reason",["API timeout after retries","model refusal","incomplete handoff"])
def test_policy_failure_places_an_actual_hold(public_case,reason):
    conn,s=public_case("P01")
    class Broken(ScriptedPolicy):
        def work(self,*args): raise PolicyFailed(reason)
    r=run_scenario(conn,s,Broken())
    assert r["decisions"][0]["failure"]==reason
    assert conn.execute("SELECT target_ref,reason FROM holds").fetchone()[0]==s.tasks[0]["event"]
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin='vouch' AND status='posted'").fetchone()[0]==0


def test_failed_tool_is_logged_and_held(public_case,monkeypatch):
    conn,s=public_case("P01")
    def broken(*args): raise RuntimeError("disk read failed")
    monkeypatch.setattr(Toolbox,"draft_entries",broken)
    r=run_scenario(conn,s)
    assert "disk read failed" in r["decisions"][0]["failure"]
    assert conn.execute("SELECT COUNT(*) FROM holds").fetchone()[0]==1
