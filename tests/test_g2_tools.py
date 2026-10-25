"""G2: typed schemas, structured errors, audited calls, and no election-changing tool."""
import pytest
from vouch.agent.tools import SPECS,Toolbox


def test_tool_inventory_matches_required_capabilities():
    names={s["name"] for s in SPECS}
    assert {"get_case_file","compute_event_tax","request_info","draft_entries","submit_decision","post_entries"} <= names
    assert not names & {"approve","change_election","file_return","run_payroll"}


@pytest.mark.parametrize("name,args",[("get_case_file",{}),("get_election",{"year":"2026"}),("draft_entries",{"amount":"1.00"}),("change_election",{})])
def test_bad_arguments_are_structured_and_logged(public_case,name,args):
    conn,s=public_case("C01");box=Toolbox(conn,s.tasks[0])
    r=box.call(name,args)
    assert r["status"]=="error" and not r["ok"]
    assert conn.execute("SELECT outcome FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()[0]=="failed"


def test_primary_read_contains_computed_proposal_not_answers(public_case):
    conn,s=public_case("P01");box=Toolbox(conn,s.tasks[0])
    data=box.call("get_case_file",{"trigger_id":s.tasks[0]["event"]})["data"]
    assert data["proposal"]["vat_recoverable"]=="6175.00" and data["attendance_count"]==360
    assert "expect" not in str(data)


def test_independent_reads_can_execute_concurrently_without_concurrent_audit_writes(public_case,monkeypatch):
    from threading import Barrier
    conn,s=public_case("C01");box=Toolbox(conn,s.tasks[0]);box.refresh()
    barrier=Barrier(2,timeout=2)
    original=box.execute
    def synchronized(name,args):
        if name in {"compute_event_tax","compute_corrections"}:
            barrier.wait()
        return original(name,args)
    monkeypatch.setattr(box,"execute",synchronized)
    results=box.call_many([("compute_event_tax",{}),("compute_corrections",{})])
    assert all(r["ok"] for r in results)
    from vouch.controls import audit
    assert audit.verify(conn).ok


def test_moving_prompt_cache_respects_api_breakpoint_limit_and_preserves_content():
    from vouch.agent.policy import cache_latest_message
    messages=[{"role":"user","content":"trigger"}]
    cache_latest_message(messages)
    assert messages[0]["content"][0]["text"]=="trigger"
    messages.append({"role":"user","content":[{"type":"tool_result","tool_use_id":"call","content":"facts"}]})
    cache_latest_message(messages)
    assert sum("cache_control" in block for msg in messages for block in msg["content"])==1
    assert messages[-1]["content"][0]["content"]=="facts"


def test_submission_requires_actual_approval_requests_not_only_planned_roles(public_case):
    conn,s=public_case("P01");box=Toolbox(conn,s.tasks[0]);box.refresh()
    assert box.call("draft_entries",{})["ok"]
    assert box.call("draft_payroll_export",{})["ok"]
    result=box.call("submit_decision",{})
    assert not result["ok"] and "approval" in result["error"]
