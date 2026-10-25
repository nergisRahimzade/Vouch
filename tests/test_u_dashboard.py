"""Layer 8 U1-U6: the dashboard reads only what a close left in the database, and every human action
goes through the Layer 5 controls as the person in that role. The agent's role can't approve, post,
release a hold or move money from the dashboard either."""
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from vouch.ui.server import create_app


def _wait(app, timeout=600):
    jobs = app.state.jobs
    deadline = time.monotonic() + timeout
    while jobs.running:
        assert time.monotonic() < deadline, "job did not finish"
        time.sleep(0.2)
    job = jobs.current
    assert job["status"] == "done", job["error"] or job["log"][-5:]
    return job


@pytest.fixture
def closed(db_path, tmp_path):
    """A private copy of the company, closed through the dashboard by the scripted playbook."""
    db = tmp_path / "vouch.db"
    shutil.copyfile(db_path, db)
    app = create_app(db, world=tmp_path / "world", results=tmp_path / "results")
    client = TestClient(app)
    assert client.post("/api/jobs/close", json={"mode": "scripted"}).status_code == 200
    _wait(app)
    return client, app


def _refusals(client, actor="Vouch"):
    return [r for r in client.get("/api/audit", params={"who": "refused", "limit": 500}).json()["rows"]
            if r["actor"] == actor and r["outcome"] == "refused"]


def test_u1_the_close_timeline_shows_every_step_of_the_run(closed):
    client, _ = closed
    overview = client.get("/api/overview").json()
    assert [s["state"] for s in overview["steps"]] == ["done"] * 8
    assert overview["run"]["mode"] == "scripted" and overview["run"]["report_status"] == "accepted"
    assert overview["audit"]["ok"] and overview["audit"]["rows"] > 0
    cases = client.get("/api/cases").json()["cases"]
    assert cases and overview["cases"]["total"] == len(cases)
    assert sum(overview["cases"]["open_by_kind"].values()) == len([c for c in cases if c["status"] != "resolved"])


def test_u2_a_case_shows_its_sources_trusted_source_rule_amount_and_trail(closed):
    client, _ = closed
    for case in client.get("/api/cases").json()["cases"]:
        detail = client.get(f"/api/cases/{case['case_id']}").json()
        finding = detail["finding"]
        if not case["case_id"].startswith("F-"):
            continue                                          # opened by the agent, not by reconciliation
        assert len(finding["evidence"]) >= 2 and finding["trusted_source"] and finding["trust_reason"]
        assert finding["rules"] and finding["amount"] is not None
        assert detail["trail"] and detail["trail"][0]["action"] == "open_case"


def test_u2_vouch_defends_a_case_from_the_audit_trail_only(closed):
    client, _ = closed
    case_id = client.get("/api/cases").json()["cases"][0]["case_id"]
    answer = client.post(f"/api/cases/{case_id}/why", json={"mode": "scripted"}).json()
    assert answer["ok"] and answer["answer"] and answer["rows"]


def test_u3_the_agent_role_cannot_approve_post_release_or_pay(closed):
    client, _ = closed
    inbox = client.get("/api/inbox").json()
    request = inbox["requests"][0]["request_id"]
    hold = inbox["holds"][0]["hold_id"]
    before = len(_refusals(client))
    attempts = [
        client.post(f"/api/requests/{request}/decide", json={"role": "vouch", "decision": "approve", "note": "ok"}),
        client.post(f"/api/requests/{request}/decide", json={"role": "vouch", "decision": "reject", "note": "no"}),
        client.post(f"/api/holds/{hold}/release", json={"role": "vouch", "note": "release"}),
        client.post("/api/payments/release", json={"role": "vouch"}),
        client.post(f"/api/entries/{request}/post", json={"role": "vouch"}),
    ]
    assert [a.status_code for a in attempts] == [403] * 5
    assert all(a.json()["refused"] and a.json()["rule"] == "ROLE" for a in attempts)
    assert len(_refusals(client)) == before + 5             # each refusal is logged before it is shown
    assert client.get("/api/audit/verify").json()["ok"]


def test_u3_approvals_follow_the_controls_in_order(closed):
    client, _ = closed
    large = next(r for r in client.get("/api/inbox").json()["requests"]
                 if r["kind"] == "entry" and r["needs"] == ["finance_manager", "controller"])
    rid, entry = large["request_id"], large["subject_ref"]
    early = client.post(f"/api/requests/{rid}/decide", json={"role": "controller", "decision": "approve", "note": "ok"})
    assert early.status_code == 403 and early.json()["rule"] == "FIN-04"
    blank = client.post(f"/api/requests/{rid}/decide", json={"role": "finance_manager", "decision": "approve", "note": ""})
    assert blank.status_code == 403                          # a decision needs a note
    first = client.post(f"/api/requests/{rid}/decide",
                        json={"role": "finance_manager", "decision": "approve", "note": "Ties to the register."})
    assert first.json() == {"request_id": rid, "status": "pending", "assigned_role": "controller", "by": "Daniel Reyes"}
    unposted = client.post(f"/api/entries/{entry}/post", json={"role": "controller"})
    assert unposted.status_code == 403                       # not posted before every approval is in
    second = client.post(f"/api/requests/{rid}/decide",
                         json={"role": "controller", "decision": "approve", "note": "Second approval."})
    assert second.json()["status"] == "approved"
    assert entry in client.get("/api/inbox").json()["ready_to_post"]
    assert client.post(f"/api/entries/{entry}/post", json={"role": "finance_manager"}).status_code == 403
    assert client.post(f"/api/entries/{entry}/post", json={"role": "controller"}).json()["status"] == "posted"
    posted = client.get(f"/api/ledger/{entry}").json()
    assert posted["entry"]["status"] == "posted" and posted["lines"]


def test_u3_a_rejection_from_the_dashboard_becomes_a_precedent(closed):
    client, app = closed
    request = next(r for r in client.get("/api/inbox").json()["requests"] if r["kind"] == "accrual")
    done = client.post(f"/api/requests/{request['request_id']}/decide",
                       json={"role": "controller", "decision": "reject", "note": "Resubmit through the controller."})
    assert done.json()["status"] == "rejected"
    from vouch.db import connect
    conn = connect(app.state.db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM rulings WHERE source = ?",
                            (f"approval:{request['request_id']}",)).fetchone()[0] == 1
    finally:
        conn.close()


def test_u3_an_answer_leads_to_the_step_the_question_promised(closed):
    client, _ = closed
    question = next(q for q in client.get("/api/inbox").json()["questions"] if q["status"] == "open")
    wrong = next(r for r in ("hr", "treasury", "stock_admin") if r != question["to_role"])
    refused = client.post(f"/api/questions/{question['question_id']}/answer",
                          json={"role": wrong, "answer": "yes", "note": "Not mine to answer."})
    assert refused.status_code == 403 and refused.json()["rule"] == "ROLE"
    done = client.post(f"/api/questions/{question['question_id']}/answer",
                       json={"role": question["to_role"], "answer": "yes", "note": "Confirmed."}).json()
    assert done["next_step"] == question["if_yes"]
    again = client.post(f"/api/questions/{question['question_id']}/answer",
                        json={"role": question["to_role"], "answer": "no", "note": "Changed my mind."})
    assert again.status_code == 403 and again.json()["rule"] == "FIN-08"
    denied = [r for r in client.get("/api/audit", params={"who": "refused", "limit": 50}).json()["rows"]
              if r["action"] == "answer" and r["basis"].get("question") == question["question_id"]]
    assert {r["basis"]["rule"] for r in denied} == {"ROLE", "FIN-08"}    # denied attempts are logged too
    assert client.get("/api/audit/verify").json()["ok"]


def test_u4_ledger_lines_link_to_their_source_rows(closed):
    client, _ = closed
    drafts = client.get("/api/ledger", params={"origin": "vouch"}).json()["entries"]
    assert drafts and all(e["status"] == "draft" for e in drafts)
    entry = client.get(f"/api/ledger/{drafts[0]['entry_id']}").json()
    assert sum(float(l["debit"]) for l in entry["lines"]) == pytest.approx(sum(float(l["credit"]) for l in entry["lines"]))
    assert any(l["sources"] and l["sources"][0]["path"] for l in entry["lines"])


def test_u5_results_show_only_saved_evaluator_reports(closed, tmp_path):
    from vouch.evaluator import run
    client, _ = closed
    assert client.get("/api/results").json() == {"reports": []}
    run.write(run.not_scored("heldout", "scripted", "no held-out cases have been written yet"), tmp_path / "results")
    reports = client.get("/api/results").json()["reports"]
    assert len(reports) == 1 and reports[0]["status"] == "not scored" and reports[0]["metrics"] is None


def test_u1_busy_approvers_are_reminded_then_escalated(closed):
    client, app = closed
    client.post("/api/jobs/follow-up", json={"business_days": 2})
    reminded = _wait(app)["result"]["actions"]
    assert reminded and {a["action"] for a in reminded} == {"reminded"}
    client.post("/api/jobs/follow-up", json={"business_days": 2})
    assert {a["action"] for a in _wait(app)["result"]["actions"]} == {"escalated"}
    assert any(r["status"] == "escalated" for r in client.get("/api/overview").json()["requests"])


def test_u1_colleagues_round_runs_through_the_controls(closed):
    client, app = closed
    client.post("/api/jobs/colleagues", json={"mode": "scripted"})
    result = _wait(app)["result"]
    assert result["answered"] and result["posted"] and result["released"]
    people = client.get("/api/audit", params={"who": "people", "limit": 500}).json()["rows"]
    assert people and all(r["role"] != "vouch" for r in people)
    assert client.get("/api/audit/verify").json()["ok"]


def test_live_model_is_refused_without_a_key(closed, monkeypatch):
    import vouch.agent.policy as policy
    client, _ = closed
    monkeypatch.setattr(policy, "api_key_available", lambda: False)
    response = client.post("/api/jobs/close", json={"mode": "model"})
    assert response.status_code == 400 and "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_u6_reset_works_while_another_program_holds_the_database_open(closed):
    import sqlite3
    client, app = closed
    other = sqlite3.connect(app.state.db)                 # a second dashboard or a database viewer
    other.execute("SELECT COUNT(*) FROM audit_log").fetchone()
    try:
        client.post("/api/jobs/rebuild", json={"variant": "main"})
        _wait(app)
    finally:
        other.close()
    overview = client.get("/api/overview").json()
    assert overview["run"] is None and overview["cases"]["total"] == 0 and overview["audit"]["rows"] == 0


def test_u6_sandbox_rebuilds_the_clean_company_and_its_close_opens_no_case(tmp_path):
    db = tmp_path / "vouch.db"
    app = create_app(db, world=tmp_path / "world", results=tmp_path / "results")
    client = TestClient(app)
    assert client.get("/api/overview").status_code == 409            # nothing loaded yet
    client.post("/api/jobs/rebuild", json={"variant": "clean"})
    _wait(app)
    early = client.post("/api/jobs/colleagues", json={"mode": "scripted"})
    assert early.status_code == 409 and "run the close first" in early.json()["detail"]
    client.post("/api/jobs/close", json={"mode": "scripted"})
    _wait(app)
    assert client.get("/api/overview").json()["cases"]["total"] == 0
