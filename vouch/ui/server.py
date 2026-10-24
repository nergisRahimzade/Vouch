"""The dashboard's API. Reads come from the database a close left behind; every human action goes
through the Layer 5 controls as the named person holding the chosen role, so a refusal here is the
same refusal the CLI gets, and it is logged in the audit trail before it is shown.

    python -m vouch ui [--port 8000]
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vouch.db import DEFAULT_DB, connect
from vouch.db.rebuild import DEFAULT_WORLD
from vouch.ui.jobs import Busy, Jobs, build_world

STATIC = Path(__file__).with_name("static")
DEFAULT_RESULTS = Path(__file__).resolve().parents[2] / "results"
ROLES = ["vouch", "finance_manager", "controller", "hr", "treasury", "stock_admin", "department_head"]
CLOSE_STEPS = ["load", "payroll", "stock_expense", "reconcile", "work_cases", "draft_entries", "request_approvals",
               "report"]


# -- request bodies ------------------------------------------------------------------
class Decide(BaseModel):
    role: str
    decision: str
    note: str = ""
    callback_confirmed: bool = False


class Answer(BaseModel):
    role: str
    answer: str
    note: str = ""


class AsRole(BaseModel):
    role: str
    note: str = ""


class Why(BaseModel):
    question: str = "Why did you do this?"
    mode: str = "scripted"
    budget_usd: float | None = None


class RebuildJob(BaseModel):
    variant: str = "main"


class CloseJob(BaseModel):
    mode: str = "scripted"
    effort: str = "medium"
    budget_usd: float | None = None


class ModeJob(BaseModel):
    mode: str = "scripted"
    budget_usd: float | None = None


class FollowUpJob(BaseModel):
    business_days: int = 2


class ScoreJob(BaseModel):
    set: str = "main"
    runs: int = 3
    mode: str = "scripted"
    effort: str = "medium"
    budget_usd: float | None = None


# -- helpers -------------------------------------------------------------------------
def _open(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def _prepare(path: Path) -> None:
    """Make sure the controls and agent tables exist, and let reads run while a close writes."""
    from vouch.agent import runner
    conn = _open(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        runner.ensure(conn)
    finally:
        conn.close()


def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor]


def _json(text, default=None):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _directory(conn) -> list[dict]:
    row = conn.execute("SELECT content FROM documents WHERE kind = 'directory'").fetchone()
    return json.loads(row[0])["people"] if row else []


def _company(conn) -> dict:
    row = conn.execute("SELECT content FROM documents WHERE kind = 'company'").fetchone()
    return json.loads(row[0]) if row else {}


def _records(conn, close_period: str) -> dict[str, dict]:
    """Every case's finding: from reconciliation, or (for cases the agent opened) from the audit trail."""
    out = {}
    if _has(conn, "findings"):
        for (record,) in conn.execute("SELECT record FROM findings WHERE close_period = ?", (close_period,)):
            item = json.loads(record)
            out[item["finding_id"]] = item
    for (target, basis) in conn.execute("SELECT target, basis FROM audit_log WHERE action = 'open_case'"):
        finding = _json(basis, {}).get("finding")
        if target not in out and isinstance(finding, dict):
            out[target] = finding
    return out


def _actor(conn, role: str, case: dict | None = None):
    from vouch.agent.colleagues import colleagues_department_head
    from vouch.controls.gates import VOUCH
    from vouch.db.periods import Actor, directory_actor
    if role not in ROLES:
        raise HTTPException(400, f"unknown role {role!r}")
    if role == "vouch":
        return VOUCH
    if role == "department_head":
        return Actor(colleagues_department_head(conn, case), role)
    return directory_actor(conn, role)


def _refused(refusal) -> JSONResponse:
    return JSONResponse({"refused": True, "rule": refusal.rule, "reason": refusal.reason}, status_code=403)


def _require_mode(mode: str) -> None:
    from vouch.agent.policy import api_key_available
    if mode not in ("model", "scripted"):
        raise HTTPException(400, f"unknown mode {mode!r}")
    if mode == "model" and not api_key_available():
        raise HTTPException(400, "the live model needs ANTHROPIC_API_KEY (environment or .env); use scripted mode, "
                                 "which every result labels as scripted")


def _budget(value):
    from vouch.agent.cost import DEFAULT_BUDGET_USD, clamp_budget
    try:
        return DEFAULT_BUDGET_USD if value is None else clamp_budget(value)
    except ValueError as error:
        raise HTTPException(400, str(error))


def _policy(mode: str, effort: str = "medium", budget=None):
    from vouch.agent.policy import ModelPolicy, ScriptedPolicy
    return ScriptedPolicy() if mode == "scripted" else ModelPolicy(effort=effort, budget_usd=_budget(budget))


# -- the app -------------------------------------------------------------------------
def create_app(db: Path = DEFAULT_DB, world: Path = DEFAULT_WORLD, results: Path = DEFAULT_RESULTS) -> FastAPI:
    app = FastAPI(title="Vouch dashboard", docs_url="/api/docs", openapi_url="/api/openapi.json")
    jobs = Jobs()
    app.state.jobs, app.state.db = jobs, db
    if db.exists():
        _prepare(db)

    def get_conn():
        if jobs.rebuilding:
            raise HTTPException(503, "the company is being rebuilt; try again in a moment")
        if not db.exists():
            raise HTTPException(409, "no database yet: rebuild the company first")
        conn = _open(db)
        try:
            if not _has(conn, "audit_log"):
                conn.close()
                _prepare(db)
                conn = _open(db)
            yield conn
        finally:
            conn.close()

    def idle():
        if jobs.running:
            raise HTTPException(409, f"{jobs.current['label']} is running; wait for it to finish")

    # ---- status and overview ----
    @app.get("/api/status")
    def status():
        from vouch.agent.policy import MODEL, api_key_available
        return {"db": db.exists(), "model_available": api_key_available(), "model": MODEL, **jobs.snapshot()}

    @app.get("/api/estimate")
    def estimate(runs: int = Query(3, ge=1, le=20)):
        """What a live run is expected to cost, and where the figure comes from."""
        from vouch.agent.cost import DEFAULT_BUDGET_USD, MAX_BUDGET_USD, MIN_BUDGET_USD, estimate_close, estimate_score
        conn = _open(db) if db.exists() and not jobs.rebuilding else None
        try:
            return {"close": estimate_close(conn), "score": estimate_score(conn, runs),
                    "default_budget": str(DEFAULT_BUDGET_USD), "min_budget": str(MIN_BUDGET_USD),
                    "max_budget": str(MAX_BUDGET_USD)}
        finally:
            if conn:
                conn.close()

    @app.get("/api/overview")
    def overview(conn=Depends(get_conn)):
        from vouch.controls import audit
        company = _company(conn)
        month = company.get("open_month")
        periods = _rows(conn.execute("SELECT period, status FROM periods ORDER BY period"))
        run = conn.execute("SELECT * FROM agent_runs ORDER BY rowid DESC LIMIT 1").fetchone()
        start = conn.execute("SELECT MAX(seq) FROM audit_log WHERE action = 'close_started'").fetchone()[0] or 0
        done = {}
        for r in conn.execute("SELECT seq, at, action, reason, basis, outcome FROM audit_log WHERE seq >= ? AND "
                              "(action LIKE 'close_step_%' OR action = 'close_report')", (start,)):
            number = 8 if r["action"] == "close_report" else int(r["action"].rsplit("_", 1)[1])
            done[number] = {"at": r["at"], "detail": r["reason"], "outcome": r["outcome"], "seq": r["seq"],
                            "basis": _json(r["basis"], {})}
        steps = [{"number": n, "name": name, **done.get(n, {}), "state": "done" if n in done else "todo"}
                 for n, name in enumerate(CLOSE_STEPS, 1)]
        if run is not None and run["status"] == "running":
            current = next((s for s in steps if s["state"] == "todo"), None)
            if current:
                current["state"] = "running"
        records = _records(conn, month) if month else {}
        cases = _rows(conn.execute("SELECT case_id, kind, status FROM cases WHERE close_period = ?", (month,)))
        entities = Counter((records.get(c["case_id"]) or {}).get("entity") or "All" for c in cases
                           if c["status"] != "resolved")
        requests = _rows(conn.execute("SELECT request_id, kind, subject_ref, amount, currency, assigned_role, status, "
                                      "created_at, case_id FROM approval_requests WHERE status IN ('pending', "
                                      "'escalated') ORDER BY created_at, request_id"))
        questions = _rows(conn.execute("SELECT question_id, case_id, to_role, to_person, fact_needed, asked_at FROM "
                                       "questions WHERE status = 'open' ORDER BY question_id"))
        check = audit.verify(conn)
        recent = _rows(conn.execute("SELECT seq, at, actor, role, action, target, outcome, reason FROM audit_log "
                                    "ORDER BY seq DESC LIMIT 8"))
        return {
            "company": {k: company.get(k) for k in ("name", "open_month", "entities", "departments")},
            "periods": periods, "run": dict(run) if run else None, "steps": steps,
            "cases": {"total": len(cases), "by_status": Counter(c["status"] for c in cases),
                      "open_by_kind": Counter(c["kind"] for c in cases if c["status"] != "resolved"),
                      "open_by_entity": entities},
            "holds": conn.execute("SELECT COUNT(*) FROM holds WHERE released_at IS NULL").fetchone()[0],
            "requests": requests, "questions": questions,
            "audit": {"ok": check.ok, "rows": check.rows, "first_bad_seq": check.first_bad_seq, "problem": check.problem},
            "suspicious_emails": conn.execute("SELECT COUNT(DISTINCT target) FROM audit_log WHERE action = "
                                              "'suspicious_instruction'").fetchone()[0],
            "refusals": conn.execute("SELECT COUNT(*) FROM audit_log WHERE outcome = 'refused'").fetchone()[0],
            "recent": recent,
            "clock": conn.execute("SELECT now FROM clock WHERE id = 1").fetchone()[0],
        }

    @app.get("/api/people")
    def people(conn=Depends(get_conn)):
        return {"roles": ROLES, "people": _directory(conn)}

    # ---- audit trail ----
    @app.get("/api/audit")
    def audit_rows(conn=Depends(get_conn), who: str = "all", q: str = "", before: int | None = None,
                   limit: int = Query(100, le=500)):
        where, params = [], []
        if who == "vouch":
            where.append("role = 'vouch' AND action NOT LIKE 'tool:%'")
        elif who == "tools":
            where.append("action LIKE 'tool:%'")
        elif who == "people":
            where.append("role <> 'vouch'")
        elif who == "refused":
            where.append("outcome IN ('refused', 'failed')")
        if q.strip():
            where.append("(target LIKE ? OR action LIKE ? OR reason LIKE ? OR actor LIKE ? OR basis LIKE ?)")
            params += [f"%{q.strip()}%"] * 5
        total = conn.execute("SELECT COUNT(*) FROM audit_log" + (" WHERE " + " AND ".join(where) if where else ""),
                             params).fetchone()[0]
        if before:
            where.append("seq < ?")
            params.append(before)
        query = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + \
            " ORDER BY seq DESC LIMIT ?"
        rows = [{**dict(r), "basis": _json(r["basis"], {})} for r in conn.execute(query, params + [limit])]
        return {"total": total, "rows": rows}

    @app.get("/api/audit/verify")
    def verify(conn=Depends(get_conn)):
        from vouch.controls import audit
        check = audit.verify(conn)
        return {"ok": check.ok, "rows": check.rows, "first_bad_seq": check.first_bad_seq, "problem": check.problem}

    # ---- cases ----
    @app.get("/api/cases")
    def cases(conn=Depends(get_conn)):
        month = _company(conn).get("open_month")
        records = _records(conn, month)
        out = []
        for c in _rows(conn.execute("SELECT * FROM cases WHERE close_period = ? ORDER BY opened_at, case_id", (month,))):
            r = records.get(c["case_id"], {})
            out.append({**c, **{k: r.get(k) for k in ("entity", "period", "amount", "currency", "trusted_source",
                                                      "rules", "ask_role", "ask_person", "proposed_action")},
                        "summary": c["summary"]})
        return {"close_period": month, "cases": out}

    @app.get("/api/cases/{case_id}")
    def case(case_id: str, conn=Depends(get_conn)):
        from vouch.controls import audit
        row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no case {case_id}")
        record = _records(conn, row["close_period"]).get(case_id, {})
        drafts = []
        for entry in _rows(conn.execute("SELECT * FROM journal_entries WHERE entry_id = ?", (f"VCH-{case_id}",))):
            entry["lines"] = _rows(conn.execute("SELECT * FROM journal_lines WHERE entry_id = ? ORDER BY line_no",
                                                (entry["entry_id"],)))
            drafts.append(entry)
        return {
            "case": dict(row), "finding": record,
            "holds": _rows(conn.execute("SELECT * FROM holds WHERE case_id = ? ORDER BY hold_id", (case_id,))),
            "questions": [{**q, "sources": _json(q["sources"], [])} for q in _rows(conn.execute(
                "SELECT * FROM questions WHERE case_id = ? ORDER BY question_id", (case_id,)))],
            "requests": [{**r, "needs": _json(r["needs"], []), "approvals": _json(r["approvals"], [])} for r in _rows(
                conn.execute("SELECT * FROM approval_requests WHERE case_id = ? ORDER BY request_id", (case_id,)))],
            "drafts": drafts,
            "trail": audit.trail(conn, case_id),
        }

    @app.post("/api/cases/{case_id}/why")
    def defend(case_id: str, body: Why, conn=Depends(get_conn), _=Depends(idle)):
        from vouch.agent.defend import why
        _require_mode(body.mode)
        policy = _policy(body.mode, budget=body.budget_usd)
        result = why(conn, case_id, body.question, policy)
        return {**result, "mode": body.mode, "cost_usd": str(policy.usage.cost(policy.model or ""))}

    # ---- human review ----
    @app.get("/api/inbox")
    def inbox(conn=Depends(get_conn)):
        month = _company(conn).get("open_month")
        records = _records(conn, month)
        summaries = {r[0]: r[1] for r in conn.execute("SELECT case_id, summary FROM cases")}
        requests = []
        for r in _rows(conn.execute("SELECT * FROM approval_requests ORDER BY CASE WHEN status IN ('pending', "
                                    "'escalated') THEN 0 ELSE 1 END, created_at, request_id")):
            record = records.get(r["case_id"] or "", {})
            requests.append({**r, "needs": _json(r["needs"], []), "approvals": _json(r["approvals"], []),
                             "payload": _json(r["payload"], {}), "case_summary": summaries.get(r["case_id"] or ""),
                             "case_kind": record.get("kind"), "department": (record.get("subject") or {}).get("department")})
        questions = [{**q, "sources": _json(q["sources"], []), "case_summary": summaries.get(q["case_id"])}
                     for q in _rows(conn.execute("SELECT * FROM questions ORDER BY CASE status WHEN 'open' THEN 0 "
                                                 "ELSE 1 END, question_id"))]
        approved_drafts = [r[0] for r in conn.execute(
            "SELECT e.entry_id FROM journal_entries e JOIN approval_requests a ON a.subject_ref = e.entry_id AND "
            "a.kind = 'entry' AND a.status = 'approved' WHERE e.status = 'draft'")]
        holds = _rows(conn.execute("SELECT * FROM holds WHERE released_at IS NULL ORDER BY hold_id"))
        return {"requests": requests, "questions": questions, "ready_to_post": approved_drafts, "holds": holds}

    @app.post("/api/requests/{request_id}/decide")
    def decide(request_id: str, body: Decide, conn=Depends(get_conn), _=Depends(idle)):
        from vouch.controls import approvals
        from vouch.controls.gates import Refused
        if body.decision not in ("approve", "reject"):
            raise HTTPException(400, "a decision is approve or reject")
        try:
            item = approvals.get(conn, request_id)
        except KeyError:
            raise HTTPException(404, f"no request {request_id}")
        actor = _actor(conn, body.role, _records(conn, _company(conn).get("open_month")).get(item["case_id"] or ""))
        try:
            result = approvals.decide(conn, actor, request_id, body.decision, body.note, body.callback_confirmed)
        except Refused as refusal:
            return _refused(refusal)
        conn.commit()
        return {"request_id": request_id, "status": result["status"], "assigned_role": result["assigned_role"],
                "by": actor.name}

    @app.post("/api/questions/{question_id}/answer")
    def answer(question_id: str, body: Answer, conn=Depends(get_conn), _=Depends(idle)):
        from vouch.agent import questions
        from vouch.agent.tools import Toolbox
        from vouch.controls.gates import Refused
        if body.answer not in ("yes", "no", "partial"):
            raise HTTPException(400, "an answer is yes, no or partial")
        if not body.note.strip():
            raise HTTPException(400, "an answer needs a note")
        row = conn.execute("SELECT case_id, to_person FROM questions WHERE question_id = ?", (question_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no question {question_id}")
        record = _records(conn, _company(conn).get("open_month")).get(row["case_id"], {})
        actor = _actor(conn, body.role, record)
        if body.role == "department_head":                  # the question names the head it was put to
            from vouch.db.periods import Actor
            actor = Actor(row["to_person"], "department_head")
        toolbox = None

        def draft(case_id):                                 # the step promised on this answer: draft and request
            nonlocal toolbox
            toolbox = toolbox or Toolbox(conn)
            entry = toolbox.draft_for(case_id)
            toolbox.call("request_approval", {"case_id": case_id, "kind": "entry", "subject_ref": entry,
                                              "note": "drafted after the answer"})
            return entry
        try:
            result = questions.answer(conn, question_id, actor, body.answer, body.note, draft=draft)
        except Refused as refusal:
            conn.rollback()
            return _refused(refusal)
        conn.commit()
        return {**result, "by": actor.name}

    @app.post("/api/entries/{entry_id}/post")
    def post_entry(entry_id: str, body: AsRole, conn=Depends(get_conn), _=Depends(idle)):
        from vouch.controls import gates
        try:
            gates.post_approved_entry(conn, _actor(conn, body.role), entry_id)
        except gates.Refused as refusal:
            return _refused(refusal)
        conn.commit()
        return {"entry_id": entry_id, "status": "posted"}

    @app.post("/api/holds/{hold_id}/release")
    def release_hold(hold_id: int, body: AsRole, conn=Depends(get_conn), _=Depends(idle)):
        from vouch.controls import gates
        if not conn.execute("SELECT 1 FROM holds WHERE hold_id = ? AND released_at IS NULL", (hold_id,)).fetchone():
            raise HTTPException(404, f"no active hold {hold_id}")
        if not body.note.strip():
            raise HTTPException(400, "releasing a hold needs a note")
        try:
            gates.release_hold(conn, _actor(conn, body.role), hold_id, body.note)
        except gates.Refused as refusal:
            return _refused(refusal)
        conn.commit()
        return {"hold_id": hold_id, "released": True}

    @app.post("/api/payments/release")
    def payment_run(body: AsRole, conn=Depends(get_conn), _=Depends(idle)):
        """The payment run: release every open-month payment that is not held. Held ones are refused by the gates."""
        from vouch.controls import gates
        from vouch.recon.sources import Sources
        actor = _actor(conn, body.role)
        try:
            gates.require(conn, actor, "release_payment", "payment_run")  # the wrong role is refused once, logged
        except gates.Refused as refusal:
            return _refused(refusal)
        month = _company(conn)["open_month"]
        released, refused = [], []
        for payment in Sources(conn).rows("payment_file", month):
            if payment["status"] != "pending_release" or conn.execute(
                    "SELECT 1 FROM payment_releases WHERE payment_id = ?", (payment["payment_id"],)).fetchone():
                continue
            try:
                gates.release_payment(conn, actor, payment["payment_id"], body.note or "released in the payment run")
                released.append(payment["payment_id"])
            except gates.Refused as refusal:
                refused.append({"payment": payment["payment_id"], "rule": refusal.rule, "reason": refusal.reason})
        conn.commit()
        return {"released": len(released), "refused": refused}

    # ---- ledger ----
    @app.get("/api/ledger")
    def ledger(conn=Depends(get_conn), period: str | None = None, entity: str = "", status: str = "",
               origin: str = ""):
        month = period or _company(conn).get("open_month")
        where, params = ["e.period = ?"], [month]
        for column, value in (("entity", entity), ("status", status), ("origin", origin)):
            if value:
                where.append(f"e.{column} = ?")
                params.append(value)
        entries = _rows(conn.execute(
            "SELECT e.*, (SELECT COUNT(*) FROM journal_lines l WHERE l.entry_id = e.entry_id) AS lines, "
            "(SELECT group_concat(debit, '|') FROM journal_lines l WHERE l.entry_id = e.entry_id) AS debits, "
            "(SELECT status FROM approval_requests a WHERE a.kind = 'entry' AND a.subject_ref = e.entry_id "
            " ORDER BY created_at DESC LIMIT 1) AS approval "
            "FROM journal_entries e WHERE " + " AND ".join(where) + " ORDER BY e.origin DESC, e.entity, e.entry_id",
            params))
        from decimal import Decimal
        for e in entries:
            e["total"] = str(sum((Decimal(d) for d in (e.pop("debits") or "").split("|") if d), Decimal("0.00")))
        return {"period": month, "periods": _rows(conn.execute("SELECT period, status FROM periods ORDER BY period")),
                "entries": entries}

    @app.get("/api/ledger/{entry_id}")
    def entry(entry_id: str, conn=Depends(get_conn)):
        row = conn.execute("SELECT * FROM journal_entries WHERE entry_id = ?", (entry_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no entry {entry_id}")
        lines = _rows(conn.execute("SELECT * FROM journal_lines WHERE entry_id = ? ORDER BY line_no", (entry_id,)))
        for line in lines:
            line["sources"] = _rows(conn.execute(
                "SELECT s.role, s.doc_id, s.row_no, d.path, d.kind, r.data FROM line_sources s JOIN documents d "
                "USING (doc_id) LEFT JOIN source_rows r ON r.doc_id = s.doc_id AND r.row_no = s.row_no "
                "WHERE s.entry_id = ? AND s.line_no = ? LIMIT 25", (entry_id, line["line_no"])))
            for s in line["sources"]:
                s["data"] = _json(s["data"])
        requests = [{**r, "approvals": _json(r["approvals"], [])} for r in _rows(conn.execute(
            "SELECT request_id, status, needs, approvals, assigned_role FROM approval_requests WHERE kind = 'entry' "
            "AND subject_ref = ?", (entry_id,)))]
        return {"entry": dict(row), "lines": lines, "requests": requests}

    # ---- results (the evaluator's saved reports; never typed by hand) ----
    @app.get("/api/results")
    def results_list():
        reports = []
        for path in sorted(results.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
            report = _json(path.read_text(encoding="utf-8"), {})
            reports.append({"file": path.name, **{k: report.get(k) for k in (
                "set", "mode", "model", "label", "status", "reason", "date", "scored_at", "runs", "seed", "cost_usd",
                "plants", "metrics", "targets", "at_target", "demo", "cases_opened")}})
        for r in reports:
            if r.get("metrics"):
                r["metrics"] = {k: v for k, v in r["metrics"].items() if k != "consistency"} | {
                    "consistency": {k: r["metrics"]["consistency"][k] for k in
                                    ("flaky", "handled_differently", "runs_at_target")}}
        return {"reports": reports}

    # ---- jobs ----
    @app.get("/api/jobs")
    def job_status():
        return jobs.snapshot()

    def start(kind: str, label: str, work):
        try:
            return jobs.start(kind, label, work)
        except Busy as busy:
            raise HTTPException(409, str(busy))

    @app.post("/api/jobs/rebuild")
    def job_rebuild(body: RebuildJob):
        from vouch.evaluator import run
        if body.variant not in run.SETS:
            raise HTTPException(400, f"the set is one of {', '.join(run.SETS)}")
        reason = run.unavailable(body.variant)
        if reason:
            raise HTTPException(400, f"the {body.variant} set cannot be built yet: {reason}")

        def work(log):
            from vouch.db.rebuild import rebuild, report
            build_world(body.variant, world, log)
            log("loading the files into a fresh database")
            # Build beside the live database, then copy it in place: the live file is never deleted, so a
            # second dashboard or a database viewer holding it open (Windows refuses to delete an open
            # file) does not stop a reset.
            fresh = db.with_name(db.name + ".rebuild")
            result = rebuild(world, fresh)
            source, target = sqlite3.connect(fresh), _open(db)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(str(fresh) + suffix).unlink(missing_ok=True)
            for line in report({**result, "db": str(db)}).splitlines():
                log(line)
            _prepare(db)
            return {"variant": body.variant, "open": result["open"]}
        return start("rebuild", f"Rebuild ({body.variant} set)", work)

    @app.post("/api/jobs/close")
    def job_close(body: CloseJob):
        _require_mode(body.mode)
        budget = _budget(body.budget_usd)

        def work(log):
            from vouch.agent.runner import run_close
            conn = _open(db)
            try:
                log(f"close started ({'SCRIPTED playbook, not the model' if body.mode == 'scripted' else 'live model'})")
                result = run_close(conn, _policy(body.mode, body.effort, budget))
            finally:
                conn.close()
            log(f"{result['run_id']}: {result['cases']} cases, {len(result['drafts'])} drafts, "
                f"{len(result['requests'])} approval requests, cost ${result['cost_usd']}")
            log("report " + ("accepted" if result["report_ok"] else "REJECTED") + " by the claim check")
            return {k: result[k] for k in ("run_id", "mode", "model", "cases", "report_ok", "cost_usd")}
        return start("close", "Close (" + ("scripted" if body.mode == "scripted" else "live model") + ")", work)

    @app.post("/api/jobs/colleagues")
    def job_colleagues(body: ModeJob, conn=Depends(get_conn)):
        _require_mode(body.mode)
        budget = _budget(body.budget_usd)
        if not _has(conn, "findings") or not conn.execute("SELECT 1 FROM agent_runs").fetchone():
            raise HTTPException(409, "there is nothing for colleagues to answer yet: run the close first")

        def work(log):
            from vouch.agent.colleagues import ModelColleagues, ScriptedColleagues, respond
            conn = _open(db)
            try:
                policy = _policy(body.mode, budget=budget)
                colleagues = ScriptedColleagues() if body.mode == "scripted" else ModelColleagues(policy, conn)
                result = respond(conn, colleagues)
            finally:
                conn.close()
            if body.mode == "model":
                log(f"cost ${policy.usage.cost(policy.model)} (cap ${policy.budget})")
            log(f"{result['mode']} colleagues: {len(result['answered'])} questions answered, {len(result['decided'])} "
                f"decisions, {len(result['posted'])} entries posted, {result['released']} payments released, "
                f"{len(result['refused'])} refused by the controls")
            return {"answered": len(result["answered"]), "decided": len(result["decided"]),
                    "posted": len(result["posted"]), "released": result["released"], "refused": len(result["refused"])}
        return start("colleagues", f"Colleagues round ({body.mode})", work)

    @app.post("/api/jobs/follow-up")
    def job_follow_up(body: FollowUpJob):
        if not 1 <= body.business_days <= 10:
            raise HTTPException(400, "advance between 1 and 10 business days")

        def work(log):
            from datetime import datetime, timedelta
            from vouch.agent import questions
            from vouch.controls import approvals, clock
            from vouch.controls.gates import VOUCH
            conn = _open(db)
            try:
                day, moved = datetime.fromisoformat(clock.now(conn)), 0
                while moved < body.business_days:
                    day += timedelta(days=1)
                    moved += day.weekday() < 5
                clock.advance(conn, days=(day - datetime.fromisoformat(clock.now(conn))).days)
                done = approvals.follow_up(conn, VOUCH) + questions.follow_up(conn)
                conn.commit()
                log(f"simulated clock now {clock.now(conn)}; {len(done)} reminders or escalations")
                return {"now": clock.now(conn), "actions": done}
            finally:
                conn.close()
        return start("follow-up", f"Advance clock {body.business_days} business days", work)

    @app.post("/api/jobs/score")
    def job_score(body: ScoreJob):
        from vouch.evaluator import run
        _require_mode(body.mode)
        if body.set not in run.SETS:
            raise HTTPException(400, f"set is one of {', '.join(run.SETS)}")
        if body.runs < run.MIN_RUNS:
            raise HTTPException(400, f"a score rests on at least {run.MIN_RUNS} runs")
        budget = _budget(body.budget_usd)

        def work(log):
            log(f"scoring the {body.set} set over {body.runs} runs on fresh copies of the company "
                f"(spending cap ${budget} per run)")
            report = run.score(body.set, body.runs, body.mode, results_dir=results, effort=body.effort,
                               budget_usd=budget)
            for line in run.render(report).splitlines():
                log(line)
            return {"report_file": report.get("report_file"), "status": report["status"]}
        return start("score", f"Score {body.set} set ({body.mode}, {body.runs} runs)", work)

    # ---- the page ----
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.exception_handler(sqlite3.OperationalError)
    def database_busy(request: Request, error: sqlite3.OperationalError):
        return JSONResponse({"detail": f"database: {error}"}, status_code=503)

    return app


def serve(host: str = "127.0.0.1", port: int = 8000, db: Path = DEFAULT_DB) -> None:
    import uvicorn
    print(f"Vouch dashboard on http://{host}:{port}  (database {db})")
    uvicorn.run(create_app(db), host=host, port=port, log_level="warning")
