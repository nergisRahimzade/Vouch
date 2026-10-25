"""Responses API contract tests using real tools and a protocol-level fake client."""
import json
from decimal import Decimal
from types import SimpleNamespace

from vouch.agent.policy import ModelPolicy
from vouch.agent.runner import run_scenario


class Output:
    def __init__(self,**data): self.__dict__.update(data)
    def model_dump(self,**kwargs): return dict(self.__dict__)


class Client:
    def __init__(self,trigger):
        self.responses=self
        self.trigger=trigger
        self.requests=[]

    def create(self,**kwargs):
        self.requests.append(kwargs)
        turn=len(self.requests)
        calls=[("get_case_file",{"trigger_id":self.trigger})] if turn==1 else (
            [(name,{}) for name in ("draft_entries","draft_payroll_export","draft_recipient_notice","request_approval")] if turn==2
            else [("submit_decision",{})])
        output=[Output(type="reasoning",id=f"reason-{turn}",encrypted_content="opaque-provider-state")]
        output += [Output(type="function_call",name=name,arguments=json.dumps(args),call_id=f"call-{turn}-{n}") for n,(name,args) in enumerate(calls)]
        return SimpleNamespace(status="completed",output=output,usage=SimpleNamespace(input_tokens=1000,
            input_tokens_details=SimpleNamespace(cached_tokens=200),output_tokens=100))


def test_gpt_uses_responses_and_preserves_reasoning_and_actual_controls(public_case):
    conn,s=public_case("P11")
    client=Client(s.tasks[0]["event"])
    policy=ModelPolicy(model="gpt-6.1-sol",client=client)
    result=run_scenario(conn,s,policy)
    decision=result["decisions"][0]
    assert decision["decision"]=="post_corrections" and decision["wage_taxable"]=="811.80"
    assert result["model"]=="gpt-6.1-sol" and result["mode"]=="model"
    assert result["metrics"]["llm_calls"]==3
    assert Decimal(result["metrics"]["cost_usd"])==Decimal("0.00786")
    assert all(not request["store"] and request["reasoning"]=={"effort":"high"} for request in client.requests)
    assert any(item.get("encrypted_content")=="opaque-provider-state" for item in client.requests[-1]["input"])
    assert all(t["type"]=="function" and t["strict"] for t in client.requests[0]["tools"])
    assert not {"approve","change_election","file_return","run_payroll"} & {t["name"] for t in client.requests[0]["tools"]}
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin='vouch' AND status='posted'").fetchone()[0]==0


def test_gpt_timeout_holds_the_actual_subject(public_case):
    import httpx
    import openai
    conn,s=public_case("P01")
    class TimeoutClient:
        @property
        def responses(self): return self
        def create(self,**kwargs): raise openai.APITimeoutError(request=httpx.Request("POST","https://api.openai.com/v1/responses"))
    result=run_scenario(conn,s,ModelPolicy(model="gpt-6.1-sol",client=TimeoutClient()))
    assert result["decisions"][0]["decision"]=="hold"
    assert "APITimeoutError" in result["decisions"][0]["failure"]
    assert conn.execute("SELECT target_ref FROM holds").fetchone()[0]==s.tasks[0]["event"]
