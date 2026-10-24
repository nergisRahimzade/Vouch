"""G5: every number, date, rule and name in the agent's reports must match a tool result;
otherwise the report is rejected."""
import json
import shutil

import pytest

from vouch.agent.claims import check
from vouch.agent.policy import ScriptedPolicy
from vouch.agent.runner import run_close
from vouch.db import connect

EVIDENCE = [json.dumps({"amount": "6025.74", "net": "1,234.50", "date": "2026-09-25", "month": "2026-09",
                        "rules": ["PAY-06"], "asked": "Priya Natarajan", "payment_id": "PAY-2026-09-DE-0033"})]
NAMES = ["Priya Natarajan", "Amara Okafor"]


def test_claims_that_match_the_evidence_pass():
    text = ("Held PAY-2026-09-DE-0033 for 6,025.74 on 2026-09-25 (2026-09) under PAY-06; asked Priya Natarajan. "
            "Net 1,234.50.")
    result = check(text, EVIDENCE, NAMES)
    assert result.ok and result.checked >= 7


@pytest.mark.parametrize("text, problem", [
    ("Held 6,025.75 for review.", "amount 6,025.75"),
    ("Held it on 2026-09-26.", "date 2026-09-26"),
    ("Booked in 2026-08.", "month 2026-08"),
    ("Under FIN-07 this is blocked.", "rule FIN-07"),
    ("Payment PAY-2026-09-DE-0034 held.", "identifier PAY-2026-09-DE-0034"),
    ("Amara Okafor approved it.", "name Amara Okafor"),
])
def test_one_wrong_claim_is_enough_to_reject(text, problem):
    result = check(text, EVIDENCE, NAMES)
    assert not result.ok and any(p.startswith(problem) for p in result.problems)


class _WrongAmount(ScriptedPolicy):
    """A report with one invented amount; it never corrects itself."""
    def report(self, facts, problems=None):
        return super().report(facts) + "\nTotal exposure: 987,654.32."


class _CorrectsItself(ScriptedPolicy):
    def report(self, facts, problems=None):
        return super().report(facts) + ("" if problems else "\nTotal exposure: 987,654.32.")


@pytest.fixture
def fresh(db_path, tmp_path):
    path = tmp_path / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    yield conn
    conn.close()


def test_a_report_with_one_wrong_amount_is_refused(fresh):
    result = run_close(fresh, _WrongAmount())
    assert not result["report_ok"] and result["report_problems"] == ["amount 987,654.32 does not appear in any tool result"]
    run = fresh.execute("SELECT report_status FROM agent_runs WHERE run_id = ?", (result["run_id"],)).fetchone()[0]
    assert run == "rejected"
    assert fresh.execute("SELECT outcome FROM audit_log WHERE action = 'close_report'").fetchone()[0] == "refused"


def test_a_rejected_report_gets_one_chance_to_correct_itself(fresh):
    result = run_close(fresh, _CorrectsItself())
    assert result["report_ok"] and "987,654.32" not in result["report"]


def test_a_case_decision_with_an_unsupported_number_is_refused(fresh):
    from vouch.agent.runner import ensure
    from vouch.agent.tools import Toolbox
    from vouch.controls import gates
    from vouch.controls.gates import VOUCH
    from vouch.recon.run import reconcile
    ensure(fresh)
    findings = reconcile(fresh)
    case = findings.cases()[0]
    gates.open_case(fresh, VOUCH, case.finding_id, "2026-09", case.kind, case.summary)
    toolbox = Toolbox(fresh)
    bad = toolbox.call("record_decision", {"case_id": case.finding_id, "status": "held",
                                           "explanation": "Held because 1,111,111.11 is missing."})
    assert not bad["ok"] and "claim check" in bad["error"]
    good = toolbox.call("record_decision", {"case_id": case.finding_id, "status": "held",
                                            "explanation": f"Held: {case.summary}"})
    assert good["ok"]
