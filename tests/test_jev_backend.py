"""Jev's typed protocol, fail-closed behavior and audited tools; no live accuracy claims."""
from types import SimpleNamespace
import httpx
import pytest
from vouch.agent.policy import ModelPolicy, PolicyFailed
from vouch.agent.runner import run_scenario
from vouch.agent import jev_backend


def response(data, status=200):
    return httpx.Response(status, json=data, request=httpx.Request("POST", "https://api.typesafe.ai/v1/systemone"))


def test_jev_typed_choice_rejects_unlisted_answer_after_one_retry(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "test")
    requests = []
    def post(*a, **kw):
        requests.append(kw)
        return response({"answers": {"decision": {"choice": "invented"}}})
    monkeypatch.setattr(jev_backend.httpx, "post", post)
    monkeypatch.setattr(jev_backend.time, "sleep", lambda _: None)
    with pytest.raises(PolicyFailed, match="hold: model error"):
        jev_backend.choose(ModelPolicy(model="jev-latest"), {}, "Choose", {"allowed": "Allowed"})
    assert len(requests) == 2
    assert all("effort" not in r["json"] and "cache_control" not in str(r["json"]) for r in requests)


def test_jev_auth_failure_pauses_with_audit_and_no_posted_entries(public_case, monkeypatch):
    conn, scenario = public_case("P11")
    monkeypatch.setenv("JEV_API_KEY", "test")
    monkeypatch.setattr(jev_backend.httpx, "post", lambda *a, **kw: response({"error": "secret provider detail"}, 401))
    run = run_scenario(conn, scenario, ModelPolicy(model="jev-latest"))
    assert run["status"] == "paused: model unavailable"
    assert run["provider"] == "jev"
    assert "secret provider detail" not in str(run)
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='model_unavailable'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin='vouch' AND status='posted'").fetchone()[0] == 0


def test_jev_records_actual_resolved_model_and_usage(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "test")
    monkeypatch.setattr(jev_backend.httpx, "post", lambda *a, **kw: response({"model":"jev-1.13.0", "answers":{
        "decision":{"type":"choice","choice":"ready"}}, "usage":{"input_tokens":100,"output_tokens":4}}))
    p = ModelPolicy(model="jev-latest")
    assert jev_backend.choose(p, {}, "Ready?", {"ready":"Ready"}) == "ready"
    assert p.model == "jev-1.13.0" and p.calls == 1 and p.usage.input_tokens == 100


def test_openrouter_key_is_sent_only_to_the_openrouter_decisions_endpoint(monkeypatch):
    monkeypatch.setenv('JEV_API_KEY','sk-or-test-not-a-real-key')
    monkeypatch.delenv('VOUCH_BASE_URL',raising=False)
    requests=[]
    def post(url,**kw):
        requests.append(url)
        return response({'model':'typesafe/jev-1.14-20261014','answers':{'decision':{'choice':'ready'}},
            'usage':{'input_tokens':100,'output_tokens':4,'cost':0.0000042}})
    monkeypatch.setattr(jev_backend.httpx,'post',post)
    p=ModelPolicy(model='jev-latest')
    assert jev_backend.choose(p,{},'Ready?',{'ready':'Ready'})=='ready'
    assert requests==['https://openrouter.ai/api/v1/systemone']
    assert p.provider_name=='openrouter' and str(p.usage.cost(p.model))=='0.0000042'


def test_expected_answer_never_enters_a_jev_request(tmp_path,monkeypatch):
    import json
    from vouch.world.scenario import parse
    from vouch.db.rebuild import rebuild
    from vouch.db import connect
    from vouch.evaluator.answer_key import load_cases
    from pathlib import Path
    raw=json.loads((Path(__file__).resolve().parents[1]/'cases'/'supplemental'/'S01.yaml').read_text())
    sentinel='PRIVATE_EXPECTATION_NOT_FOR_THE_AGENT'
    raw['expect']['private_canary']=sentinel
    scenario=parse(raw)
    assert not hasattr(scenario,'expect')
    payloads=[]
    monkeypatch.setenv('JEV_API_KEY','test')
    def post(url,**kw):
        payloads.append(kw['json'])
        criteria=kw['json']['questions']['decision']['criteria']
        selected='classify_and_book' if 'classify_and_book' in criteria else next(iter(criteria))
        return response({'model':'jev-test','answers':{'decision':{'choice':selected}},'usage':{'input_tokens':100,'output_tokens':4}})
    monkeypatch.setattr(jev_backend.httpx,'post',post)
    db=tmp_path/'case.db';rebuild(db,scenario)
    with connect(db) as conn:
        result=run_scenario(conn,scenario,ModelPolicy(model='jev-latest'))
    assert not result['decisions'][0].get('failure')
    assert len(payloads)>=3
    encoded=json.dumps(payloads)
    assert sentinel not in encoded and '"expect"' not in encoded and '"private_canary"' not in encoded
