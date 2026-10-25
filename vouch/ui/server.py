"""Spec ?10 REST + SSE API, serving the frontend imported from origin/frontend."""
import asyncio
import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from vouch.agent.policy import api_key_available
from vouch.agent.runner import ensure
from vouch.controls import approvals, audit, gates
from vouch.db import DEFAULT_DB, connect
from vouch.db.periods import Actor, person_actor
from vouch.db.rebuild import rebuild
from vouch.recon.run import prepass, plain, staff_state, visible_gifts, benefit_totals
from vouch.recon.sources import Sources
from vouch.ui.jobs import Runs
from vouch.world.scenario import load_dir, parse

ROOT = Path(__file__).resolve().parents[2]


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str | None = None
    custom: dict | None = None
    mode: str = "scripted"
    model: str | None = None
    effort: str = "medium"


class Decide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str
    reason: str
    actor_id: str | None = None


def custom_scenario(data):
    from decimal import Decimal
    from vouch.world.generator import expand
    kind = data.get("event_type")
    if kind not in {"staff_event", "hospitality", "gift", "promo_item", "working_meal", "mixed"}:
        raise ValueError("unsupported event_type")
    day = str(data.get("date") or datetime.now(ZoneInfo("America/Los_Angeles")).date())
    event_id = "CUSTOM-" + uuid4().hex[:10]
    people = data.get("attendees", [])
    if isinstance(people, int):
        if not 0 <= people <= 450:
            raise ValueError("integer attendees must be an actual employee count between zero and 450")
        attendance = {"event": event_id, "employees": [f"E{n:03d}" for n in range(1, people + 1)]}
    elif isinstance(people, dict):
        attendance = {**people, "event": event_id}
    else:
        raise ValueError("attendees must be actual counts or structured attendance")
    invoices = []
    for n, given in enumerate(data.get("invoices") or [], 1):
        raw = dict(given)
        if any(isinstance(v, float) for v in raw.values()):
            raise ValueError("money must be supplied as decimal strings, not floats")
        raw.update(id=raw.get("id") or f"{event_id}-INV-{n}", event=event_id,
                   invoice_date=raw.get("invoice_date") or day, received_at=raw.get("received_at") or day,
                   delivery_date=raw.get("delivery_date") or day)
        if not raw.get("lines"):
            raw["lines"] = [{k: raw[k] for k in ("net", "vat", "gross", "vat_rate", "qty", "unit_net", "recipient") if k in raw} |
                            {"category": "gift" if kind == "gift" else "promo_item" if kind == "promo_item" else "catering"}]
        if not raw.get("entity"):
            raw["entity"] = "ENT-DE"
        invoices.append(raw)
    event = {"id": event_id, "date": day, "name": data.get("name") or "Custom event", "type_declared": kind,
             "open_to": data.get("open_to", "all"), "organizer": data.get("organizer", "P-HR-EVT"),
             "purpose": data.get("purpose"), "location": data.get("location"), "host": data.get("host"),
             "exceptional_assignment": kind == "working_meal"}
    return parse({"id": event_id, "as_of": data.get("as_of") or day, "setup": {"events": [event],
                  "attendance": [attendance], "invoices": invoices}, "tasks": [{"type": "process_event", "event": event_id}]})


def create_app(db_path=DEFAULT_DB, *, run_root=None, scenarios_dir=None, results_dir=None):
    app = FastAPI(title="Vouch event tax")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
    db_path = Path(db_path)
    if not db_path.exists():
        rebuild(db_path)
    with closing(connect(db_path)) as conn:
        ensure(conn)
    registry = Runs(run_root or db_path.parent / ".vouch" / "runs")
    app.state.runs = registry
    directory = Path(scenarios_dir or ROOT / "cases" / "public")
    scenarios = {s.id: s for s in load_dir(directory)}
    results_dir = Path(results_dir or ROOT / "results")

    def open_db():
        return closing(connect(registry.latest or db_path))

    @app.get("/api/scenarios")
    def get_scenarios():
        return [{"id": s.id, "title": s.title, "as_of": s.as_of} for s in scenarios.values()]

    @app.post("/api/runs")
    def start(body: StartRun):
        if bool(body.scenario_id) == bool(body.custom):
            raise HTTPException(422, "provide exactly one of scenario_id or custom")
        if body.effort not in {"low","medium","high","max"}:
            raise HTTPException(422,"unsupported effort")
        if body.mode not in {"scripted", "model"}:
            raise HTTPException(422, "mode must be scripted or model")
        if body.mode == "model" and not api_key_available(body.model):
            raise HTTPException(400, "The selected model requires its provider API key in the environment or .env")
        try:
            scenario = scenarios[body.scenario_id] if body.scenario_id else custom_scenario(body.custom)
        except KeyError:
            raise HTTPException(404, "scenario not found")
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error))
        return {"run_id": registry.start(scenario, body.mode == "scripted", body.model, body.effort)}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        try:
            snapshot = registry.snapshot(run_id)
        except KeyError:
            raise HTTPException(404, "run not found")
        result = snapshot.get("result")
        return {**snapshot, "decision": result["decisions"][0] if result and result["decisions"] else None}

    @app.get("/api/runs/{run_id}/stream")
    def stream(run_id: str):
        try:
            registry.snapshot(run_id)
        except KeyError:
            raise HTTPException(404, "run not found")
        async def events():
            index = 0
            while True:
                snapshot = registry.snapshot(run_id)
                for step in snapshot["timeline"][index:]:
                    yield "data: " + json.dumps(step) + "\n\n"
                    index += 1
                if snapshot["status"] != "running":
                    if snapshot["status"] == "failed":
                        yield "data: " + json.dumps({"step": index+1,"kind":"flag","title":"Run failed",
                            "detail":snapshot["error"],"amounts":{},"cites":[],"severity":"needs_person"}) + "\n\n"
                    break
                await asyncio.sleep(0.1)
        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})

    @app.get("/api/events")
    def events():
        with open_db() as conn:
            src = Sources(conn)
            return list(src.events.values())

    @app.get("/api/events/{event_id}")
    def event(event_id: str):
        with open_db() as conn:
            try:
                context = prepass(conn, {"type":"process_event", "event":event_id})
            except ValueError as error:
                raise HTTPException(404, str(error))
            return {"event":context["event"],"invoices":context["invoices"],
                    "attendance_summary":{"total":len(context["attendance"]),
                        **{k:sum(a["person_kind"]==k for a in context["attendance"]) for k in ("employee","companion","business_partner","external")}},
                    "per_participant":context["decision"].get("per_participant"),
                    "regimes":context["decision"],"missing_docs":[f for f in context["findings"] if f.get("request")]}

    @app.get("/api/people/{person_id}/ledger")
    def person_ledger(person_id: str, year: int = 2026):
        with open_db() as conn:
            src=Sources(conn)
            if not any(p["id"]==person_id for p in src.data["employees"]+src.data["business_partners"]):
                raise HTTPException(404,"person not found")
            ledger=staff_state(src,year)
            rows=[{"event":ev,**plain(row)} for (person,ev),row in ledger.rows.items() if person==person_id]
            gifts,_=visible_gifts(src,year)
            person_gifts=[g for g,_,_ in gifts if g.recipient==person_id]
            from vouch.calc import eventtax as et
            gift_ledger=et.gift_ledger(person_gifts,year,src.rules).to_dict()
            limit=src.rules.value("R-GIFT-VER",f"{year}-12-31")
            return {"person_id":person_id,"year":year,"rows":rows,"gifts":gift_ledger,
                    "allowance_assignment":ledger.assignment.get(person_id,[]),
                    "totals":{"taxable":fmt(sum((row["taxable"] for (person,_),row in ledger.rows.items() if person==person_id),Decimal(0))),
                              "gift_value":gift_ledger.get("total","0.00"),
                              "benefit_37b_gross":fmt(benefit_totals(src,year).get(person_id,Decimal(0)))},
                    "limits":{"staff_event_allowance":fmt(src.rules.value("R-BV-ALLOW",f"{year}-12-31")),
                              "staff_event_count":int(src.rules.value("R-BV-MAX",f"{year}-12-31")),
                              "gift":fmt(limit),"flat_37b_cap":fmt(src.rules.value("R-37B-CAP",f"{year}-12-31"))},
                    "corrections":[dict(r) for r in conn.execute("SELECT * FROM payroll_exports WHERE employee=? AND corrects IS NOT NULL",(person_id,))]}

    @app.get("/api/approvals")
    def inbox():
        with open_db() as conn:
            return [approvals.get(conn,r[0]) for r in conn.execute("SELECT request_id FROM approval_requests WHERE status IN ('pending','escalated')")]

    @app.post("/api/approvals/{request_id}")
    def decide(request_id: str, body: Decide):
        with open_db() as conn:
            try:
                item=approvals.get(conn,request_id)
                if body.actor_id:
                    person=conn.execute("SELECT name,role FROM people WHERE id=?",(body.actor_id,)).fetchone()
                    if not person:
                        raise HTTPException(403,"unknown human actor")
                    actor=Actor(person[0],person[1])
                else:
                    actor=person_actor(conn,item["assigned_role"])
                result=approvals.decide(conn,actor,request_id,body.decision,body.reason)
                conn.commit()
                return result
            except KeyError:
                raise HTTPException(404,"approval not found")
            except gates.Refused as error:
                raise HTTPException(403,str(error))
            except ValueError as error:
                raise HTTPException(422,str(error))

    def year_window(conn, from_year, to_year):
        as_of=conn.execute("SELECT value FROM scenario WHERE key='as_of'").fetchone()[0]
        last=to_year if to_year is not None else int(as_of[:4])
        first=from_year if from_year is not None else last-9
        if not (1 <= first <= last <= 9999) or last-first > 99:
            raise HTTPException(422,"provide a valid year window of at most 100 years")
        return first,last,as_of

    @app.get("/api/books")
    def books(from_year: int | None=None,to_year: int | None=None):
        with open_db() as conn:
            first,last,as_of=year_window(conn,from_year,to_year)
            counts={}
            for row in conn.execute("SELECT substr(entry_date,1,4) AS year,status,COUNT(*) AS count "
                                    "FROM journal_entries WHERE entry_date>=? AND entry_date<=? GROUP BY year,status",
                                    (f"{first:04d}-01-01",f"{last:04d}-12-31")):
                counts.setdefault(int(row["year"]),{})[row["status"]]=row["count"]
            return {"from_year":first,"to_year":last,"as_of":as_of,"currency":"EUR",
                    "years":[{"year":year,"posted_entries":counts.get(year,{}).get("posted",0),
                              "draft_entries":counts.get(year,{}).get("draft",0),
                              "status":"recorded" if counts.get(year) else "no_recorded_entries"}
                             for year in range(first,last+1)]}

    @app.get("/api/entries")
    def entries(from_year: int | None=None,to_year: int | None=None):
        with open_db() as conn:
            first,last,_=year_window(conn,from_year,to_year)
            return [{**dict(r),"lines":[dict(l) for l in conn.execute("SELECT * FROM journal_lines WHERE entry_id=?",(r["entry_id"],))]}
                    for r in conn.execute("SELECT * FROM journal_entries WHERE entry_date>=? AND entry_date<=? ORDER BY entry_date,entry_id",
                                          (f"{first:04d}-01-01",f"{last:04d}-12-31"))]

    @app.get("/api/payroll-export")
    def payroll():
        with open_db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM payroll_exports ORDER BY id")]

    @app.get("/api/audit")
    def audit_trail():
        with open_db() as conn:
            return {"verified":audit.verify(conn).ok,"rows":audit.trail(conn)}

    @app.get("/api/scores")
    def scores():
        reports=[]
        for path in sorted(results_dir.glob("public-*.json")):
            report=json.loads(path.read_text(encoding="utf-8"))
            reports.append(report)
        return max(reports,key=lambda r:r["date"]) if reports else {"status":"not_scored"}

    frontend=ROOT/"frontend"
    if frontend.exists():
        app.mount("/",StaticFiles(directory=frontend,html=True),name="frontend")
    return app


def serve(host="127.0.0.1",port=8000,db_path=DEFAULT_DB):
    import uvicorn
    uvicorn.run(create_app(db_path),host=host,port=port)


# Imports used by deterministic ledger serialization only.
from decimal import Decimal
from vouch.money import fmt
