"""G6: a tool error, a missing rate, a timeout, or a half-finished handoff from a colleague leads to a
held case with a plain reason, never a guess."""
import json
import shutil
from types import SimpleNamespace

import pytest

from vouch.agent import tools as tools_module
from vouch.agent.policy import ModelPolicy, ScriptedPolicy
from vouch.agent.runner import ensure, run_close
from vouch.calc import fx
from vouch.controls import gates
from vouch.controls.gates import VOUCH
from vouch.db import connect
from vouch.recon.run import reconcile
from vouch.world import config as C
from test_c1_gates import actor


@pytest.fixture
def fresh(db_path, tmp_path):
    path = tmp_path / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    yield conn
    conn.close()


def case_status(conn, kind):
    return conn.execute("SELECT status, note FROM cases WHERE kind = ?", (kind,)).fetchone()


def test_a_broken_tool_holds_the_case_with_a_plain_reason(fresh, monkeypatch):
    def broken(self, **kwargs):
        raise RuntimeError("disk read failed")
    monkeypatch.setattr(tools_module.Toolbox, "t_request_funding", broken)
    result = run_close(fresh, ScriptedPolicy())
    status, note = case_status(fresh, "payments_not_cleared")
    assert status == "held" and "request_funding tool failed" in note and "disk read failed" in note
    assert any("disk read failed" in reason for reason in result["failures"].values())
    assert fresh.execute("SELECT COUNT(*) FROM approval_requests WHERE kind = 'funding'").fetchone()[0] == 0
    logged = fresh.execute("SELECT outcome FROM audit_log WHERE action = 'tool:request_funding'").fetchone()[0]
    assert logged == "failed"


def test_a_missing_exchange_rate_holds_the_conversion(fresh, monkeypatch):
    daily = {d: row for d, row in fx._daily().items() if not d.startswith(C.OPEN_MONTH)}
    monkeypatch.setattr(fx, "_daily", lambda: daily)
    result = run_close(fresh, ScriptedPolicy())
    holds = gates.active_holds(fresh, "conversion")
    assert {h["target_ref"] for h in holds} == {f"UK:{C.OPEN_MONTH}", f"DE:{C.OPEN_MONTH}"}
    assert all("no recorded rate" in h["reason"] for h in holds)
    drafts = {r[0] for r in fresh.execute("SELECT entry_id FROM journal_entries WHERE origin = 'vouch'")}
    assert f"VCH-{C.OPEN_MONTH}-UK-STOCKCOMP" not in drafts and f"VCH-{C.OPEN_MONTH}-US-STOCKCOMP" in drafts
    assert set(result["drafts"]) == drafts - {d for d in drafts if d.startswith("VCH-F-")}


class _FakeClient:
    """Stands in for the API: raises what the SDK raises, or returns a refusal."""
    def __init__(self, behaviour):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.behaviour = behaviour

    def create(self, **kwargs):
        import anthropic
        import httpx2
        if self.behaviour == "timeout":
            raise anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
        usage = SimpleNamespace(input_tokens=10, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason="refusal", content=[], usage=usage)


@pytest.mark.parametrize("behaviour, reason", [("timeout", "did not answer in time"), ("refusal", "declined")])
def test_a_model_timeout_or_refusal_holds_the_case(fresh, behaviour, reason):
    ensure(fresh)
    policy = ModelPolicy(client=_FakeClient(behaviour))
    policy.report = ScriptedPolicy().report                         # the report itself is not under test here
    result = run_close(fresh, policy)
    assert len(result["failures"]) == result["cases"] == 14
    notes = [r[0] for r in fresh.execute("SELECT note FROM cases")]
    assert all(n.startswith("held: ") and reason in n for n in notes)
    assert fresh.execute("SELECT COUNT(*) FROM holds").fetchone()[0] == 0           # nothing guessed, nothing done
    assert fresh.execute("SELECT COUNT(*) FROM journal_entries WHERE entry_id LIKE 'VCH-F-%'").fetchone()[0] == 0


def test_a_half_finished_handoff_is_finished_not_repeated(fresh):
    ensure(fresh)
    findings = reconcile(fresh)
    case = next(f for f in findings.cases() if f.kind == "bank_details_differ_from_hr")
    gates.open_case(fresh, VOUCH, case.finding_id, C.OPEN_MONTH, case.kind, case.summary, case.to_record())
    colleague = actor(fresh, "finance_manager")
    fresh.execute("INSERT INTO holds (case_id, target_kind, target_ref, reason, placed_by, placed_at) VALUES "
                  "(?, 'payment', ?, 'started by a colleague before handing over', ?, '2026-10-01T08:30:00')",
                  (case.finding_id, case.subject["payment_id"], colleague.name))
    gates.set_case(fresh, colleague, case.finding_id, "held", "handed over half-done: hold placed, HR not asked yet")
    fresh.commit()
    run_close(fresh, ScriptedPolicy())
    holds = fresh.execute("SELECT placed_by FROM holds WHERE case_id = ?", (case.finding_id,)).fetchall()
    assert [h[0] for h in holds] == [colleague.name]                              # the colleague's hold kept, not doubled
    asked = fresh.execute("SELECT to_role FROM questions WHERE case_id = ?", (case.finding_id,)).fetchall()
    assert [a[0] for a in asked] == ["hr"]                                         # the missing step was done
    found = fresh.execute("SELECT basis FROM audit_log WHERE action = 'earlier_work_found' AND target = ?",
                          (case.finding_id,)).fetchone()
    assert found and json.loads(found[0])["work"] == ["hold"]
