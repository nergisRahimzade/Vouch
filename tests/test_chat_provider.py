"""Chat Completions providers (DeepSeek, Kimi): same tools and limits; malformed calls and outages end safely."""
import json
from types import SimpleNamespace

import httpx
import openai

from vouch.agent.chat_backend import MODEL_ERROR, PAUSED
from vouch.agent.policy import ModelPolicy
from vouch.agent.runner import run_scenario


def reply(calls):
    tool_calls = [SimpleNamespace(id=f"c{n}", function=SimpleNamespace(name=name, arguments=args))
                  for n, (name, args) in enumerate(calls)]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=tool_calls),
                                                    finish_reason="tool_calls")],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10, prompt_cache_hit_tokens=50))


class Script:
    def __init__(self, turns):
        self.turns, self.requests = list(turns), []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return reply(turn)


def policy(monkeypatch, client, backup=None):
    monkeypatch.setenv("VOUCH_PROVIDER", "deepseek"); monkeypatch.setenv("VOUCH_API_KEY", "test")
    if backup:
        monkeypatch.setenv("VOUCH_BACKUP_PROVIDER", "kimi"); monkeypatch.setenv("VOUCH_BACKUP_API_KEY", "test")
    p = ModelPolicy(client=client)
    assert p.provider == "chat" and p.model == "deepseek-v4-pro"
    return p


def test_chat_provider_books_p11_with_code_amounts_and_no_anthropic_features(public_case, monkeypatch):
    conn, s = public_case("P11")
    client = Script([[("get_case_file", json.dumps({"trigger_id": "EVT-SOM26"}))],
                     [(n, "{}") for n in ("draft_entries", "draft_payroll_export", "draft_recipient_notice")]
                     + [("request_approval", json.dumps({"approvers": ["tax_manager", "controller"], "reason": "corrections"}))],
                     [("submit_decision", json.dumps({"decision": "post_corrections", "classification": "staff_event",
                       "flags": ["event_cost_threshold_crossed", "out_of_period"], "cites": ["R-BV-VAT"],
                       "rationale": "Late costs block the input VAT reclaimed earlier."}))]])
    result = run_scenario(conn, s, policy(monkeypatch, client))
    d = result["decisions"][0]
    assert d["wage_taxable"] == "811.80" and d["decision"] == "post_corrections"
    assert result["provider"] == "deepseek" and result["model"] == "deepseek-v4-pro"
    assert all("cache_control" not in json.dumps(r) and "output_config" not in r for r in client.requests)
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='model_call' AND basis LIKE '%deepseek-v4-pro%'").fetchone()[0] == 3


def test_malformed_tool_call_is_returned_once_then_held_as_model_error(public_case, monkeypatch):
    conn, s = public_case("C01")
    client = Script([[("get_case_file", "{not json")], [("get_case_file", json.dumps({"wrong": 1}))]])
    d = run_scenario(conn, s, policy(monkeypatch, client))["decisions"][0]
    assert d["decision"] == "hold" and d["failure"].startswith(MODEL_ERROR)
    assert any("malformed arguments" in str(m.get("content")) for m in client.requests[1]["messages"] if m["role"] == "tool")


def test_unavailable_primary_falls_back_to_backup_then_pauses(public_case, monkeypatch):
    conn, s = public_case("C01")
    down = openai.APIConnectionError(request=httpx.Request("POST", "https://bad.host/v1/chat/completions"))
    client = Script([down, down])
    p = policy(monkeypatch, client, backup=True)
    p.use = lambda cfg, c=None: (setattr(p, "model", cfg["model"]), setattr(p, "provider_name", cfg["provider"]))
    result = run_scenario(conn, s, p)
    assert result["status"] == PAUSED and result["decisions"][0]["decision"] == "hold"
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE action LIKE 'model_%' ORDER BY seq")]
    assert actions == ["model_switch", "model_unavailable"]
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin='vouch'").fetchone()[0] == 0
