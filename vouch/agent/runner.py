"""The close. Code runs a fixed checklist; the policy decides what to do with each case.

    1 load files   2 run payroll per entity   3 run stock expense   4 reconcile
    5 work each case   6 draft entries   7 request approvals   8 report

It runs with no human input until approvals. Running it again finds what is already done
(by itself or a colleague) and finishes only the rest."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from vouch.agent import claims, questions, templates
from vouch.agent.policy import PolicyFailed
from vouch.agent.tools import MONEY_WORDS, Toolbox, instruction_flags
from vouch.calc import fx
from vouch.calc.pay import YTD_KEYS, calculate_pay
from vouch.controls import approvals, audit, gates
from vouch.controls.gates import VOUCH, Refused
from vouch.money import ZERO, fmt
from vouch.recon.run import reconcile
from vouch.recon.sources import Sources

NOT_OWED = {"paid_not_in_hr", "paid_after_exit", "entity_differs_from_hr",     # people left out of the accrual
            "net_pay_not_positive"}


def ensure(conn: sqlite3.Connection) -> None:
    gates.ensure(conn)
    conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    conn.commit()


def _step(conn, number: int, name: str, detail: str, basis: dict | None = None) -> None:
    audit.log(conn, VOUCH, f"close_step_{number}", name, "done", detail, basis)
    conn.commit()


def run_close(conn: sqlite3.Connection, policy) -> dict:
    ensure(conn)
    src = Sources(conn)
    month = src.open_month
    run_id = f"RUN-{conn.execute('SELECT COUNT(*) FROM agent_runs').fetchone()[0] + 1:03d}"
    conn.execute("INSERT INTO agent_runs (run_id, close_period, mode, model, started_at, status) VALUES (?, ?, ?, ?, ?, "
                 "'running')", (run_id, month, policy.mode, policy.model,
                                datetime.now(timezone.utc).isoformat(timespec="seconds")))
    audit.log(conn, VOUCH, "close_started", month, "done", f"{policy.mode} run {run_id}",
              {"run": run_id, "mode": policy.mode, "model": policy.model})

    # 1 load
    documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    if not documents:
        raise RuntimeError("nothing is loaded; run `python -m vouch rebuild` first")
    _step(conn, 1, "load", f"{documents} documents loaded for {month}", {"documents": documents})

    # 2 payroll per entity, recomputed with the pay function
    payroll = {}
    for entity in src.entities:
        totals, differences = defaultdict(lambda: ZERO), 0
        for row in src.rows("payroll_register", month, entity):
            keys = YTD_KEYS[entity]
            base = {k: Decimal(row["gross_pay"]) if k == "gross" else Decimal(row[f"period_{k}"]) for k in keys}
            before = {k: Decimal(row[f"ytd_{k}"]) - base[k] for k in keys}
            result = calculate_pay(entity, int(row["tax_year"]), int(row["tax_period"]), Decimal(row["gross_pay"]),
                                   before, employer_pension_rate=src.pension_rate(entity),
                                   days=(int(row["days_paid"]), int(row["days_in_month"])))
            totals["gross"] += result.gross
            totals["net"] += result.net
            totals["employer_costs"] += result.employer_total
            differences += result.net != Decimal(row["net_pay"])
        payroll[entity] = {k: fmt(v) for k, v in totals.items()} | {"rows_differing_from_register": differences}
    _step(conn, 2, "payroll", "payroll recomputed per entity", payroll)

    # 3 stock expense, from signed terms and HR dates
    stock_usd = defaultdict(lambda: ZERO)
    for expense, _, entity, _ in templates.grant_expense(src, month).values():
        stock_usd[entity] += expense
    _step(conn, 3, "stock_expense", "stock expense computed per entity (USD)", {e: fmt(v) for e, v in stock_usd.items()})

    # 4 reconcile
    findings = reconcile(conn)
    cases = [f.to_record() for f in findings.cases()]
    for case in cases:
        gates.open_case(conn, VOUCH, case["finding_id"], month, case["kind"], case["summary"], case)
    _step(conn, 4, "reconcile", f"{len(cases)} cases, {len(findings.explained())} explained differences",
          {"cases": [c["finding_id"] for c in cases]})

    # 5 work each case
    toolbox = Toolbox(conn)
    toolbox.call("list_cases", {})
    failures = {}
    for case in cases:
        cid = case["finding_id"]
        status = conn.execute("SELECT status FROM cases WHERE case_id = ?", (cid,)).fetchone()[0]
        if status == "resolved":
            audit.log(conn, VOUCH, "case_skipped", cid, "done", "already resolved; nothing left to do")
            continue
        started = _work_started(conn, cid)
        if started:
            # Someone (an earlier run, or a colleague) started this case: keep what is done, finish the rest.
            audit.log(conn, VOUCH, "earlier_work_found", cid, "done",
                      f"found earlier work ({', '.join(started)}); finishing only what is missing", {"work": started})
        tool_calls = len(toolbox.corpus)
        try:
            policy.work(toolbox.cases[cid], toolbox)
        except PolicyFailed as failure:
            failures[cid] = str(failure)
            gates.set_case(conn, VOUCH, cid, "held", f"held: {failure}", {"failure": str(failure)})
            continue
        broken = [json.loads(r)["error"] for r in toolbox.corpus[tool_calls:] if json.loads(r).get("tool_error")]
        if broken:
            failures[cid] = broken[0]
            gates.set_case(conn, VOUCH, cid, "held", f"held: {broken[0]}", {"tool_errors": broken})
    conn.commit()
    _step(conn, 5, "work_cases", f"{len(cases) - len(failures)} cases worked, {len(failures)} held after a failure",
          {"failures": failures})

    # 6 draft the open month's entries
    drafted, held_conversions = [], []
    exclude = {c["subject"].get("employee_id") for c in cases if c["kind"] in NOT_OWED and c["period"] == month}
    held_grants = {h["target_ref"] for h in gates.active_holds(conn, "grant")} | {
        g for c in cases if c["kind"] == "entity_differs_from_hr" for g in _grants_of(src, c["subject"]["employee_id"])}
    for entity in src.entities:
        for name, build in (("payroll", lambda e: templates.payroll_accrual(src, e, exclude)),
                            ("stock", lambda e: templates.stock_expense(src, e, held_grants))):
            try:
                entry = build(entity)
                if not conn.execute("SELECT 1 FROM journal_entries WHERE entry_id = ?", (entry.entry_id,)).fetchone():
                    gates.draft_entry(conn, VOUCH, entry)
                drafted.append(entry.entry_id)
            except fx.MissingFxRate as missing:
                case_id = f"FX-{month}-{entity}"
                gates.place_hold(conn, VOUCH, case_id, "conversion", f"{entity}:{month}", f"held: {missing}")
                held_conversions.append(case_id)
            except (templates.CannotDraft, Refused) as reason:
                audit.log(conn, VOUCH, "draft_skipped", f"{name}:{entity}", "done", str(reason))
    conn.commit()
    _step(conn, 6, "draft_entries", f"{len(drafted)} open-month entries drafted", {"drafts": drafted,
                                                                                  "held_conversions": held_conversions})

    # 7 request approval for every draft not yet sent
    requested = []
    for (entry_id, entity) in conn.execute("SELECT entry_id, entity FROM journal_entries WHERE origin = 'vouch' AND "
                                           "status = 'draft' AND period = ?", (month,)).fetchall():
        amount = sum((Decimal(r[0]) for r in conn.execute("SELECT debit FROM journal_lines WHERE entry_id = ?",
                                                          (entry_id,))), ZERO)
        case_id = entry_id[4:] if entry_id[4:] in toolbox.cases else None
        try:
            requested.append(approvals.request(conn, VOUCH, "entry", entry_id, {"entry": entry_id}, amount,
                                               src.currency(entity), case_id, reason="draft ready for approval"))
        except Refused as refusal:
            audit.log(conn, VOUCH, "request_skipped", entry_id, "done", refusal.reason)
    conn.commit()
    _step(conn, 7, "request_approvals", f"{len(requested)} approval requests open", {"requests": requested})

    # 8 report, claim-checked. Instructions found in emails are reported and logged; nothing acts on them.
    for email in suspicious_emails(src):
        toolbox.call("read_email", {"email_id": email["email_id"]})
    facts = _facts(conn, src, policy, cases, drafted, held_conversions, findings)
    toolbox.corpus.append(json.dumps(facts, default=str))
    names = [p["name"] for p in src.content["directory"]["people"]]
    report, check = policy.report(facts), None
    check = claims.check(report, toolbox.corpus, names)
    if not check.ok:
        report = policy.report(facts, check.problems)
        check = claims.check(report, toolbox.corpus, names)
    usage = policy.usage
    conn.execute("UPDATE agent_runs SET finished_at = ?, status = 'done', input_tokens = ?, output_tokens = ?, "
                 "cache_read_tokens = ?, cost_usd = ?, report = ?, report_status = ?, report_problems = ? WHERE run_id = ?",
                 (datetime.now(timezone.utc).isoformat(timespec="seconds"), usage.input_tokens, usage.output_tokens,
                  usage.cache_read_tokens, str(usage.cost(policy.model or "")), report,
                  "accepted" if check.ok else "rejected", json.dumps(check.problems), run_id))
    audit.log(conn, VOUCH, "close_report", month, "done" if check.ok else "refused",
              "report accepted by the claim check" if check.ok else "report rejected by the claim check",
              {"run": run_id, "problems": check.problems})
    conn.commit()
    return {"run_id": run_id, "mode": policy.mode, "model": policy.model, "cases": len(cases), "failures": failures,
            "drafts": drafted, "requests": requested, "report": report, "report_ok": check.ok,
            "report_problems": check.problems, "cost_usd": str(usage.cost(policy.model or "")),
            "tokens": {"input": usage.input_tokens, "output": usage.output_tokens, "cache_read": usage.cache_read_tokens}}


def _work_started(conn, case_id: str) -> list[str]:
    work = []
    if [h for h in gates.active_holds(conn) if h["case_id"] == case_id]:
        work.append("hold")
    if conn.execute("SELECT 1 FROM questions WHERE case_id = ?", (case_id,)).fetchone():
        work.append("question")
    if conn.execute("SELECT 1 FROM approval_requests WHERE case_id = ?", (case_id,)).fetchone():
        work.append("request")
    if conn.execute("SELECT 1 FROM journal_entries WHERE entry_id = ?", (f"VCH-{case_id}",)).fetchone():
        work.append("draft")
    return work


def _grants_of(src: Sources, employee_id: str) -> set[str]:
    return {r["grant_id"] for r in src.rows("signed_grants") if r["employee_id"] == employee_id}


def suspicious_emails(src: Sources) -> list[dict]:
    """Emails carrying instructions: reported, never acted on."""
    out = []
    for email in src.rows("inbox"):
        words = instruction_flags(email["body"])
        external = not email["from"].endswith("@" + src.company["domain"])
        if words or (external and MONEY_WORDS.search(email["body"])):
            out.append({"email_id": email["email_id"], "from": email["from"], "subject": email["subject"],
                        "words": words, "external": external})
    return out


def suspicious_notes(conn) -> list[dict]:
    """Colleagues' free-text notes (answers and approval decisions) that carry instructions: reported, never acted on."""
    notes = [(r["question_id"], r["answered_by"], r["note"]) for r in conn.execute(
        "SELECT question_id, answered_by, note FROM questions WHERE note IS NOT NULL")]
    for r in conn.execute("SELECT request_id, approvals, note FROM approval_requests"):
        notes += [(r["request_id"], a["actor"], a.get("note", "")) for a in json.loads(r["approvals"])]
        notes += [(r["request_id"], "decision", r["note"])] if r["note"] else []
    return [{"ref": ref, "by": by, "words": instruction_flags(text)} for ref, by, text in notes if instruction_flags(text)]


def _facts(conn, src, policy, cases, drafted, held_conversions, findings) -> dict:
    case_list = []
    for case in cases:
        row = conn.execute("SELECT status, note FROM cases WHERE case_id = ?", (case["finding_id"],)).fetchone()
        asked = [dict(q) for q in conn.execute("SELECT question_id, to_person, fact_needed FROM questions WHERE "
                                               "case_id = ?", (case["finding_id"],))]
        case_list.append({"case_id": case["finding_id"], "kind": case["kind"], "period": case["period"],
                          "amount": case["amount"], "currency": case["currency"], "status": row["status"],
                          "note": row["note"], "asked": asked, "rules": case["rules"]})
    suspicious = suspicious_emails(src)
    for email in suspicious:
        audit.log(conn, VOUCH, "suspicious_instruction", email["email_id"], "done",
                  "instruction found in an email; treated as data and not acted on", email)
    return {"close_period": src.open_month, "mode": policy.mode, "model": policy.model, "cases": len(cases),
            "explained": len(findings.explained()), "case_list": case_list, "drafts": drafted,
            "held_conversions": held_conversions,
            "pending_requests": [dict(r) for r in conn.execute(
                "SELECT request_id, kind, subject_ref, amount, currency, assigned_role FROM approval_requests WHERE "
                "status IN ('pending', 'escalated')")],
            "suspicious_emails": [e["email_id"] for e in suspicious],
            "suspicious_notes": sorted({n["ref"] for n in suspicious_notes(conn)})}
