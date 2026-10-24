"""G2: the tools as sent to the model are a request the API accepts, and every tool whose input reaches a
calculation or a control keeps a strict schema."""
from vouch.agent.policy import MAX_STRICT_TOOLS, READ_PREFIXES, model_tools
from vouch.agent.tools import SPECS


def test_the_model_request_stays_within_the_strict_tool_limit():
    tools = model_tools()
    assert [t["name"] for t in tools] == [s["name"] for s in SPECS]
    assert sum(t["strict"] for t in tools) <= MAX_STRICT_TOOLS


def test_compute_and_act_tools_are_strict():
    for tool in model_tools():
        if not tool["name"].startswith(READ_PREFIXES):
            assert tool["strict"], tool["name"]
    names = {t["name"] for t in model_tools() if t["strict"]}
    assert {"hold", "ask", "draft_correction", "request_approval", "request_funding", "record_decision",
            "compute_pay", "translate"} <= names
