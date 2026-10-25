"""Trigger flow with deterministic pre-pass, bounded model work, and auditable safe failure."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from vouch.agent.policy import ScriptedPolicy, PolicyFailed
from vouch.agent.tools import Toolbox
from vouch.controls import audit, gates
from vouch.recon.run import prepass
from vouch.recon.sources import Sources


def ensure(conn):
    gates.ensure(conn)
    conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    conn.commit()


def follow_up(conn):
    """Spec §5.8 switched on: one reminder after 24 business hours, then an escalation, for every unanswered
    question and approval, on the simulated clock. Recorded in the audit trail."""
    from vouch.agent import questions
    from vouch.controls import approvals
    actions = questions.follow_up(conn) + approvals.follow_up(conn, gates.VOUCH)
    conn.commit()
    return actions


def expand_tasks(conn, tasks):
    src = Sources(conn)
    out = []
    for task in tasks:
        kind = task.get("type") or task.get("kind") or "process_event"
        if kind in {"run_period_close", "run_year_end"}:
            out += [{"type":"process_event", "event":e["id"]} for e in src.events.values()
                    if e["date"] <= src.as_of and e["date"][:4] == src.as_of[:4]
                    and (src.invoices(e["id"]) or src.attendance(e["id"]))]
            out += [{"type":"process_invoice", "invoice":i["id"]} for i in src.data["invoices"]
                    if not i["matched_event"] and i["id"] not in src.booked_invoices()]
        elif kind in {"process_email", "process_attendance"}:
            target = task.get("event") or task.get("event_id")
            if not target and kind == "process_attendance":
                rid = task.get("attendance_id") or task.get("attendance") or task.get("target")
                row = next((r for r in src.data["attendance"] if r["id"] == rid), None)
                target = row["event"] if row else None
            if target:
                out.append({"type":"process_event", "event":target})
                continue
            if kind == "process_email":
                rid = task.get("email_id") or task.get("email") or task.get("target")
                email = next((r for r in src.data["emails"] if r["id"] == rid), None)
                if not email:
                    raise ValueError("email trigger is not visible")
                target = email.get("event") or email.get("event_id")
                if target in src.events:
                    out.append({"type":"process_event", "event":target})
                    continue
                text = ((email.get("subject") or "") + " " + (email.get("body") or "")).casefold()
                referenced = [i for i in src.data["invoices"] if i["id"].casefold() in text or
                              i["id"] == (email.get("invoice") or email.get("invoice_id"))]
                if not referenced:
                    events = [e for e in src.events.values() if e["id"].casefold() in text or
                              (e.get("name") and e["name"].casefold() in text)]
                    if len(events) == 1:
                        out.append({"type":"process_event", "event":events[0]["id"]})
                        continue
                    # Only a unique visible new invoice received in the email's period can be selected implicitly.
                    referenced = [i for i in src.data["invoices"] if i["id"] not in src.booked_invoices()
                                  and str(i.get("received_at") or "")[:7] == str(email.get("received_at") or "")[:7]]
                if len(referenced) == 1:
                    out.append({"type":"process_invoice", "invoice":referenced[0]["id"]})
                    continue
            raise ValueError("trigger does not identify an unambiguous visible event or invoice; request clarification")
        else:
            out.append(task)
    unique = {}
    for task in out:
        key = (task.get("type"), task.get("event") or task.get("event_id") or task.get("invoice")
               or task.get("invoice_id") or task.get("target") or task.get("id"))
        unique.setdefault(key, task)
    return list(unique.values())


def run_trigger(conn, task, policy=None, responses=(), emit=None):
    ensure(conn)
    policy = policy or ScriptedPolicy()
    toolbox = Toolbox(conn, task, responses, emit)
    try:
        context = toolbox.refresh()
    except (ValueError, KeyError, ArithmeticError, PermissionError, sqlite3.Error) as error:
        # The trigger could not even be read: stop safely, hold it, and record why (spec §8.3).
        target = str(task.get("event") or task.get("event_id") or task.get("invoice") or task.get("invoice_id")
                     or task.get("target") or task.get("id") or "trigger")
        gates.place_hold(conn, gates.VOUCH, target, "trigger", target, f"{type(error).__name__}: {error}")
        audit.log(conn, gates.VOUCH, "agent_failure", target, "failed", f"{type(error).__name__}: {error}",
                  {"task": task, "stage": "pre-pass"})
        conn.commit()
        return {"case_id": target, "task": target, "decision": "hold", "failure": f"{type(error).__name__}: {error}",
                "flags": [], "approvers": [], "entries": []}, toolbox
    case = context["decision"]
    gates.open_case(conn, gates.VOUCH, case["case_id"], Sources(conn).open_month, case["classification"],
                    case["rationale"], {"proposal": case, "rules": context["rules"], "invoices": context["invoices"],
                                        "attendance":context["attendance"],"payroll_inputs":context["payroll"]})
    try:
        policy.work(case, toolbox)
        if toolbox.submitted is None:
            raise PolicyFailed("policy returned without submit_decision")
    except (PolicyFailed, ValueError, KeyError) as error:
        gates.place_hold(conn, gates.VOUCH, case["case_id"], "event", case["matched_event"] or case["case_id"], str(error))
        gates.set_case(conn, gates.VOUCH, case["case_id"], "held", str(error))
        audit.log(conn, gates.VOUCH, "agent_failure", case["case_id"], "failed", str(error))
        conn.commit()
        return {"case_id": case["case_id"], "task": case["task"], "decision": "hold", "failure": str(error),
                "flags": case["flags"], "approvers": [], "entries": []}, toolbox
    status = "held" if toolbox.submitted["decision"] in {"hold", "escalate"} else "waiting" if toolbox.drafts else "resolved"
    gates.set_case(conn, gates.VOUCH, case["case_id"], status, toolbox.submitted["rationale"])
    audit.log(conn, gates.VOUCH, "submit_decision", case["case_id"], "done", toolbox.submitted["rationale"],
              {**toolbox.submitted, "decided_by": {"mode": policy.mode, "model": policy.model,
                                                   "effort": getattr(policy, "effort", None)},
               "code_recommendation": {k: toolbox.candidate.get(k) for k in ("decision", "classification", "flags", "approvers")}})
    conn.commit()
    return toolbox.submitted, toolbox


def run_scenario(conn, scenario, policy=None, emit=None):
    ensure(conn)
    policy = policy or ScriptedPolicy()
    started, began = perf_counter(), datetime.now(timezone.utc).isoformat()
    run_id = "RUN-" + uuid4().hex
    conn.execute("INSERT INTO agent_runs (run_id,mode,model,started_at,status) VALUES (?,?,?,?,'running')",
                  (run_id, policy.mode, policy.model, began))
    decisions, tool_seconds = [], 0
    follow_ups = follow_up(conn)          # whatever came due before this run is reminded or escalated first
    work = [t for t in scenario.tasks if (t.get("type") or t.get("kind")) != "run_follow_up"]
    for task in expand_tasks(conn, work):
        decision, toolbox = run_trigger(conn, task, policy, scenario.people_responses, emit)
        decisions.append(decision)
        tool_seconds += toolbox.tool_seconds
    for task in scenario.tasks:
        if (task.get("type") or task.get("kind")) == "run_follow_up":
            # Time passes (simulated) and unanswered questions and approvals are reminded, then escalated.
            from vouch.controls import clock
            clock.advance(conn, hours=float(task.get("advance_hours") or 0))
            follow_ups += follow_up(conn)
    cost = policy.usage.cost(policy.model) if policy.mode == "model" else 0
    metrics = {"wall_seconds": perf_counter() - started, "llm_calls": policy.calls,
               "input_tokens": policy.usage.input_tokens, "output_tokens": policy.usage.output_tokens,
               "cache_read_input_tokens": policy.usage.cache_read_tokens,
               "cache_creation_input_tokens": policy.usage.cache_write_tokens,
               "tool_seconds": tool_seconds, "cost_usd": str(cost) if cost is not None else None}
    from vouch.agent.chat_backend import PAUSED
    paused = any(str(d.get("failure", "")).startswith(PAUSED) for d in decisions)
    result = {"run_id": run_id, "mode": policy.mode, "model": policy.model, "date": began,
              "provider": getattr(policy, "provider_name", None) or getattr(policy, "provider", "scripted"),
              "status": PAUSED if paused else "done",
              "decisions": decisions, "metrics": metrics, "effort":getattr(policy,"effort",None),
              "follow_ups": follow_ups}
    if any((task.get("type") or task.get("kind")) == "run_year_end" for task in scenario.tasks):
        from decimal import Decimal
        from vouch.money import ZERO,fmt
        from vouch.recon.run import staff_state,benefit_totals,visible_gifts
        from vouch.calc.eventtax import flat_tax_37b_available
        import hashlib
        src=Sources(conn)
        year=int(src.as_of[:4])
        if src.election(year):
            gift_rows,_=visible_gifts(src,year)
            for person,total in benefit_totals(src,year).items():
                if not flat_tax_37b_available(total,src.rules,src.as_of).values["available"]:
                    continue
                items={invoice for gift,invoice,_ in gift_rows if gift.recipient==person}
                for event in src.events.values():
                    if event["date"][:4]==str(year) and any(a["person_id"]==person for a in src.attendance(event["id"])):
                        items.update(i["id"] for i in src.invoices(event["id"]) if any(l.get("category")=="program" for l in i["lines"]))
                items=sorted(items)
                notice_id="YEAR-NOTICE-"+hashlib.sha256(f"{year}:{person}".encode()).hexdigest()[:20]
                conn.execute("INSERT OR IGNORE INTO recipient_notices (id,recipient,year,items,status) VALUES (?,?,?,?,'draft')",
                             (notice_id,person,year,json.dumps(items)))
        summary=[]
        for status in ("posted","draft"):
            for account in ("6410","6430","6460"):
                amount=sum((Decimal(row[0])-Decimal(row[1]) for row in conn.execute(
                    "SELECT l.debit,l.credit FROM journal_lines l JOIN journal_entries e USING(entry_id) "
                    "WHERE e.status=? AND e.period LIKE ? AND l.account=?",(status,f"{year}-%",account))),ZERO)
                summary.append({"account":account,"status":status,"amount":fmt(amount)})
        # §5.1 step 14: flat-taxed staff-event pay of the year must reach payroll by Feb 28 of the next year.
        deadline=f"{year+1}-02-28"
        open_flat=[dict(row) for row in conn.execute("SELECT id,employee,event,taxable_amount,status FROM payroll_exports "
                   "WHERE period LIKE ? AND method='flat_40' AND status<>'accepted'",(f"{year}-%",))]
        deadlines=[]
        if open_flat:
            total=sum((Decimal(r["taxable_amount"]) for r in open_flat),ZERO)
            deadlines.append({"flag":"payroll_flat_tax_deadline","deadline":deadline,"open_rows":len(open_flat),
                              "taxable_total":fmt(total),"events":sorted({r["event"] for r in open_flat}),
                              "rules":["R-BV-FLAT"],"to":"payroll"})
            payroll_id=next((p["id"] for p in src.data["people"] if p["role"]=="payroll"),None)
            if payroll_id:
                from vouch.agent import questions
                questions.request_info(conn,f"YEAR-END-{year}",payroll_id,"payroll_export")
            audit.log(conn,gates.VOUCH,"deadline",f"YEAR-END-{year}","flagged",
                      f"{len(open_flat)} flat-tax payroll rows ({fmt(total)}) are not yet accepted by payroll; deadline {deadline}",
                      deadlines[0])
        result["year_end"]={"year":year,"non_deductible_summary":summary,
            "allowance_assignment":staff_state(src,year).assignment,
            "payroll_exports":[dict(row) for row in conn.execute("SELECT * FROM payroll_exports WHERE period LIKE ?",(f"{year}-%",))],
            "recipient_notices":[dict(row) for row in conn.execute("SELECT * FROM recipient_notices WHERE year=?",(year,))],
            "deadlines":deadlines}
        audit.log(conn,gates.VOUCH,"year_end_summary",str(year),"done","Prepared annual summaries and notice drafts; nothing filed or paid",
                  {"non_deductible_summary":summary,"notice_count":len(result["year_end"]["recipient_notices"]),"deadlines":deadlines})
    metrics["wall_seconds"] = perf_counter() - started
    conn.execute("UPDATE agent_runs SET status=?, result=?,metrics=? WHERE run_id=?",
                  (PAUSED if paused else "failed" if any("failure" in d for d in decisions) else "done",
                   json.dumps(result), json.dumps(metrics), run_id))
    conn.commit()
    return result
