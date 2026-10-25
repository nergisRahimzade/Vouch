"""U/T11: API contract, isolated jobs, SSE, human review and the imported UI."""
import json
import time
import pytest
from fastapi.testclient import TestClient
from vouch.ui.server import create_app


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path/"api.db")
    with TestClient(app) as client: yield client


def completed(client,case_id="P11"):
    r=client.post("/api/runs",json={"scenario_id":case_id})
    assert r.status_code==200,r.text
    rid=r.json()["run_id"]
    for _ in range(500):
        run=client.get(f"/api/runs/{rid}").json()
        if run["status"]!="running":
            assert run["status"]=="done",run
            return rid,run
        time.sleep(0.01)
    raise AssertionError("run did not finish")


def test_frontend_is_served_and_scenarios_do_not_expose_answers(client):
    html=client.get("/")
    assert html.status_code==200 and "Every event" in html.text
    assert "DECISIONS = [" not in client.get("/app.js").text
    data=client.get("/api/scenarios").json()
    assert len(data)==15 and all("expect" not in x for x in data)


def test_sse_and_final_result_share_the_real_timeline(client):
    rid,run=completed(client)
    assert run["decision"]["wage_taxable"]=="811.80"
    response=client.get(f"/api/runs/{rid}/stream")
    assert response.status_code==200 and response.headers["content-type"].startswith("text/event-stream")
    steps=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert steps==run["timeline"]
    assert all({"step","kind","title","detail","amounts","cites","severity"}<=set(s) for s in steps)
    assert client.get("/api/audit").json()["verified"]


def test_human_approval_records_a_reason_and_does_not_run_payroll(client):
    completed(client,"P01")
    pending=client.get("/api/approvals").json()
    assert pending
    rid=pending[0]["request_id"]
    assert client.post(f"/api/approvals/{rid}",json={"decision":"approve","reason":"","actor_id":"P-TAX"}).status_code==403
    assert client.post(f"/api/approvals/{rid}",json={"decision":"approve","reason":"Payroll cannot approve","actor_id":"P-PAY"}).status_code==403
    assert client.post(f"/api/approvals/{rid}",json={"decision":"approve","reason":"Reviewed sources","actor_id":"P-TAX"}).status_code==200
    assert all(r["status"]=="draft" for r in client.get("/api/payroll-export").json() if r["origin"]=="vouch")


def test_custom_scenario_uses_the_same_trigger_flow(client):
    r=client.post("/api/runs",json={"custom":{"event_type":"staff_event","attendees":2,
            "invoices":[{"net":"100.00","vat":"19.00","gross":"119.00","vat_rate":"0.19"}]}})
    assert r.status_code==200,r.text
    rid=r.json()["run_id"]
    for _ in range(500):
        run=client.get(f"/api/runs/{rid}").json()
        if run["status"]!="running": break
        time.sleep(0.01)
    assert run["status"]=="done",run
    assert run["decision"]["per_participant"]=="59.50"


def test_unknown_run_and_invalid_body_fail_cleanly(client):
    assert client.get("/api/runs/missing").status_code==404
    assert client.get("/api/runs/missing/stream").status_code==404
    assert client.post("/api/runs",json={}).status_code==422


def test_cors_accepts_the_teammates_react_origin(client):
    r=client.options("/api/runs",headers={"Origin":"http://localhost:5173","Access-Control-Request-Method":"POST"})
    assert r.headers["access-control-allow-origin"]=="http://localhost:5173"


def test_books_shows_ten_calendar_years_without_fabricating_history(client):
    books=client.get("/api/books").json()
    assert len(books["years"])==10
    assert books["to_year"]-books["from_year"]==9
    assert all(y["status"]=="no_recorded_entries" for y in books["years"][:-1])
    assert client.get("/api/entries?from_year=2017&to_year=2025").json()==[]
    assert client.get("/api/entries?from_year=2026&to_year=2026").json()
    assert client.get("/api/entries?from_year=2026&to_year=2025").status_code==422
    assert "Last ten years of books" in client.get("/").text
