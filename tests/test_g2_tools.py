"""G2: read, compute and act tools; each returns a structured result with warnings; every call is
logged in the audit trail."""
import json

import pytest

from vouch.agent.runner import ensure
from vouch.agent.tools import SPECS, Toolbox
from vouch.controls import gates
from vouch.controls.gates import VOUCH
from vouch.recon.run import reconcile
from vouch.world import config as C

READ = {"list_cases", "get_case", "get_person", "get_payroll", "get_payments", "get_bank", "get_grant", "get_ledger",
        "get_policy", "get_precedents", "read_email"}
COMPUTE = {"compute_pay", "compute_stock_expense", "translate"}
ACT = {"open_case", "hold", "ask", "draft_correction", "request_approval", "request_funding", "record_decision"}


@pytest.fixture
def toolbox(controls_db):
    ensure(controls_db)
    findings = reconcile(controls_db)
    for f in findings.cases():
        gates.open_case(controls_db, VOUCH, f.finding_id, C.OPEN_MONTH, f.kind, f.summary)
    return Toolbox(controls_db)


def case_of(toolbox, kind):
    return next(c for c in toolbox.cases.values() if c["kind"] == kind)


def test_the_tool_set_covers_read_compute_and_act():
    names = {s["name"] for s in SPECS}
    assert names == READ | COMPUTE | ACT
    for spec in SPECS:
        schema = spec["input_schema"]
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
        assert spec["description"]


def test_every_tool_call_is_logged_with_a_structured_result(toolbox):
    conn = toolbox.conn
    bank_case = case_of(toolbox, "bank_details_differ_from_hr")
    pension = case_of(toolbox, "contribution_rate_differs_from_policy")
    grant = case_of(toolbox, "grant_without_board_approval")["subject"]["grant_id"]
    payment = bank_case["subject"]["payment_id"]
    calls = [
        ("list_cases", {}), ("get_case", {"case_id": bank_case["finding_id"]}),
        ("get_person", {"employee_id": bank_case["subject"]["employee_id"]}),
        ("get_payroll", {"employee_id": bank_case["subject"]["employee_id"]}),
        ("get_payments", {"employee_id": bank_case["subject"]["employee_id"]}),
        ("get_bank", {"reference": payment}), ("get_grant", {"grant_id": grant}),
        ("get_policy", {"rule_id": "PAY-06"}), ("get_precedents", {"kind": "bank_change_by_email"}),
        ("read_email", {"email_id": bank_case["subject"]["email_id"]}),
        ("compute_pay", {"employee_id": bank_case["subject"]["employee_id"], "month": C.OPEN_MONTH,
                         "entity": bank_case["entity"]}),
        ("compute_stock_expense", {"grant_id": grant, "month": C.OPEN_MONTH}),
        ("translate", {"amount_usd": "100.00", "currency": "GBP", "month": C.OPEN_MONTH}),
        ("hold", {"case_id": bank_case["finding_id"], "target_kind": "payment", "target_ref": payment,
                  "reason": "bank details differ from HR"}),
        ("ask", {"case_id": bank_case["finding_id"], "to_role": "hr", "fact_needed": "Did a call-back confirm it?",
                 "sources_disagree": ["payment_file", "hr_census"], "if_yes": "release_hold", "if_no": "keep_hold",
                 "if_partial": "ask_follow_up"}),
        ("draft_correction", {"case_id": pension["finding_id"]}),
        ("get_ledger", {"entry_id": f"VCH-{pension['finding_id']}"}),
        ("request_approval", {"case_id": pension["finding_id"], "kind": "entry",
                              "subject_ref": f"VCH-{pension['finding_id']}", "note": "pension at the policy rate"}),
        ("open_case", {"subject_ref": "an email", "kind": "suspicious_instruction", "summary": "asks to approve"}),
        ("record_decision", {"case_id": bank_case["finding_id"], "status": "held", "explanation": "held for HR"}),
    ]
    before = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action LIKE 'tool:%'").fetchone()[0]
    for name, args in calls:
        result = toolbox.call(name, args)
        assert result["ok"], (name, result)
        assert isinstance(result["warnings"], list)
    logged = conn.execute("SELECT action, basis FROM audit_log WHERE action LIKE 'tool:%' ORDER BY seq").fetchall()[before:]
    assert [r[0] for r in logged] == [f"tool:{name}" for name, _ in calls]
    assert all(json.loads(r[1])["input"] == args for r, (_, args) in zip(logged, calls))


def test_failures_and_refusals_are_structured_and_logged(toolbox):
    conn = toolbox.conn
    case = case_of(toolbox, "bank_details_differ_from_hr")
    unknown = toolbox.call("no_such_tool", {})
    no_entry = toolbox.call("draft_correction", {"case_id": case["finding_id"]})
    resolve = toolbox.call("hold", {"case_id": case["finding_id"], "target_kind": "payment",
                                    "target_ref": "PAY-NOT-IN-THIS-CASE", "reason": "x"})
    assert not unknown["ok"] and not no_entry["ok"] and not resolve["ok"]
    outcomes = [r[0] for r in conn.execute("SELECT outcome FROM audit_log WHERE action LIKE 'tool:%' ORDER BY seq "
                                           "DESC LIMIT 3")]
    assert outcomes == ["failed", "failed", "failed"]


def test_warnings_flag_what_the_agent_should_not_trust(toolbox):
    case = case_of(toolbox, "bank_details_differ_from_hr")
    email = toolbox.call("read_email", {"email_id": case["subject"]["email_id"]})
    assert any("untrusted" in w for w in email["warnings"]) and any("outside" in w for w in email["warnings"])
    ghost = case_of(toolbox, "paid_not_in_hr")
    person = toolbox.call("get_person", {"employee_id": ghost["subject"]["employee_id"]})
    assert person["found"] is False and person["warnings"]
