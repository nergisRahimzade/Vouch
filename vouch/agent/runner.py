"""Trigger flow with deterministic pre-pass, bounded model work, and auditable safe failure."""
import json
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
        key = (task.get("type"), task.get("event") or task.get("invoice") or task.get("target"))
        unique.setdefault(key, task)
    return list(unique.values())


def run_trigger(conn, task, policy=None, responses=(), emit=None):
    ensure(conn)
    policy = policy or ScriptedPolicy()
    toolbox = Toolbox(conn, task, responses, emit)
    context = toolbox.refresh()
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
    audit.log(conn, gates.VOUCH, "submit_decision", case["case_id"], "done", toolbox.submitted["rationale"], toolbox.submitted)
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
    for task in expand_tasks(conn, scenario.tasks):
        decision, toolbox = run_trigger(conn, task, policy, scenario.people_responses, emit)
        decisions.append(decision)
        tool_seconds += toolbox.tool_seconds
    cost = policy.usage.cost(policy.model) if policy.mode == "model" else 0
    metrics = {"wall_seconds": perf_counter() - started, "llm_calls": policy.calls,
               "input_tokens": policy.usage.input_tokens, "output_tokens": policy.usage.output_tokens,
               "cache_read_input_tokens": policy.usage.cache_read_tokens,
               "cache_creation_input_tokens": policy.usage.cache_write_tokens,
               "tool_seconds": tool_seconds, "cost_usd": str(cost) if cost is not None else None}
    result = {"run_id": run_id, "mode": policy.mode, "model": policy.model, "date": began,
              "decisions": decisions, "metrics": metrics}
    conn.execute("UPDATE agent_runs SET status=?, result=?,metrics=? WHERE run_id=?",
                  ("failed" if any("failure" in d for d in decisions) else "done", json.dumps(result), json.dumps(metrics), run_id))
    conn.commit()
    return result
