"""python -m vouch rebuild              build the world from the seed and load it
   python -m vouch close [--scripted]   run the open month's close (the real model by default)
   python -m vouch score [...]          score the agent on a fresh copy of the company (see vouch.evaluator)
   python -m vouch ui [--port 8000]     the dashboard (see vouch.ui)"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vouch.agent.cost import DEFAULT_BUDGET_USD, clamp_budget, estimate_close
from vouch.db import DEFAULT_DB, connect
from vouch.db.rebuild import rebuild, report
from vouch.world.build import DEFAULT_ROOT, build


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vouch")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("rebuild", help="generate the world and rebuild the database")
    c = sub.add_parser("close", help="run the open month's close")
    c.add_argument("--scripted", action="store_true", help="fixed playbook instead of the model (fast tests only)")
    c.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    c.add_argument("--budget", default=str(DEFAULT_BUDGET_USD), help="spending cap in USD for this run (default %(default)s)")
    c.add_argument("--yes", action="store_true", help="start the live model without asking first")
    c.add_argument("--record", type=Path, help="also save the model's replies to this file (for free replays)")
    c.add_argument("--replay", type=Path, help="replay a recorded live run for free instead of calling the model")
    t = sub.add_parser("try-case", help="work one kind of case live on a throwaway copy and show what it decided")
    t.add_argument("kind", help="a kind of case, e.g. bank_details_differ_from_hr")
    t.add_argument("--budget", default="1.00", help="spending cap in USD (default %(default)s)")
    t.add_argument("--yes", action="store_true")
    g = sub.add_parser("colleagues", help="demo: simulated approvers answer questions and decide requests")
    g.add_argument("--scripted", action="store_true", help="scripted colleagues instead of the model")
    g.add_argument("--budget", default=str(DEFAULT_BUDGET_USD), help="spending cap in USD (default %(default)s)")
    a = sub.add_parser("audit", help="demo: the auditor asks Vouch to defend a few cases")
    a.add_argument("--scripted", action="store_true")
    a.add_argument("--cases", type=int, default=3)
    a.add_argument("--budget", default=str(DEFAULT_BUDGET_USD), help="spending cap in USD (default %(default)s)")
    w = sub.add_parser("why", help="ask Vouch to defend how it handled a case, from the audit trail")
    w.add_argument("case_id")
    w.add_argument("--question", default="Why did you do this?")
    w.add_argument("--scripted", action="store_true")
    w.add_argument("--budget", default=str(DEFAULT_BUDGET_USD), help="spending cap in USD (default %(default)s)")
    sub.add_parser("score", help="score the agent against the hidden key (python -m vouch score --help)")
    u = sub.add_parser("ui", help="the dashboard: audit trail, human review, cases, ledger, results")
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--port", type=int, default=8000)
    if sys.argv[1:2] == ["score"]:                       # everything after 'score' belongs to the evaluator
        from vouch.evaluator.__main__ import main as evaluate
        evaluate(sys.argv[1:])
        return
    args = parser.parse_args()
    if args.command == "ui":
        from vouch.ui.server import serve
        serve(args.host, args.port, args.db)
        return
    if args.command in ("colleagues", "audit"):
        from vouch.agent.colleagues import ModelColleagues, ScriptedColleagues, auditor_review, respond
        from vouch.agent.policy import ModelPolicy, ScriptedPolicy
        conn = connect(args.db)
        policy = ScriptedPolicy() if args.scripted else ModelPolicy(budget_usd=clamp_budget(args.budget))
        colleagues = ScriptedColleagues() if args.scripted else ModelColleagues(policy, conn)
        if args.command == "colleagues":
            result = respond(conn, colleagues)
            print(f"{result['mode']} colleagues: {len(result['answered'])} questions answered, "
                  f"{len(result['decided'])} decisions, {len(result['posted'])} entries posted by the controller, "
                  f"{result['released']} payments released, {len(result['refused'])} refused by the controls")
            for item in result["answered"]:
                print(f"  {item['question']}: {item['answer']} -> {item['next_step']}")
            if not args.scripted:
                print(f"cost ${policy.usage.cost(policy.model)} (cap ${policy.budget})")
        else:
            for item in auditor_review(conn, colleagues, policy, args.cases):
                print(f"Q ({item['case_id']}): {item['question']}\nA: {item['answer'] or item['problems']}\n")
        return
    if args.command == "why":
        from vouch.agent.defend import why
        from vouch.agent.policy import ModelPolicy, ScriptedPolicy
        policy = ScriptedPolicy() if args.scripted else ModelPolicy(budget_usd=clamp_budget(args.budget))
        result = why(connect(args.db), args.case_id, args.question, policy)
        print(result["answer"] if result["ok"] else f"answer rejected by the claim check: {result['problems']}")
        if not args.scripted:
            print(f"cost ${policy.usage.cost(policy.model)} (cap ${policy.budget})")
        return
    if args.command == "try-case":
        from vouch.agent.trycase import try_case
        budget = clamp_budget(args.budget)
        print(f"one {args.kind} case, live model, on a throwaway copy of {args.db.name}; spending cap ${budget}")
        if not args.yes and (not sys.stdin.isatty() or input("Start? [y/N] ").strip().lower() != "y"):
            print("not started" + ("" if sys.stdin.isatty() else " (not a terminal: pass --yes to start)"))
            sys.exit(2)
        print(json.dumps(try_case(args.db, args.kind, budget), indent=1))
        return
    if args.command == "rebuild":
        world = build(DEFAULT_ROOT)
        print(f"world: {world['files']} files, {world['plants']} planted mistakes, {world['noise']} noise items")
        print(report(rebuild(DEFAULT_ROOT, args.db)))
    elif args.command == "close":
        from vouch.agent.policy import ModelPolicy, ScriptedPolicy
        from vouch.agent.runner import run_close
        conn = connect(args.db)
        if args.replay:
            from vouch.agent.policy import replay_policy
            result = run_close(conn, replay_policy(args.replay, effort=args.effort))
            print(f"{result['run_id']}: REPLAY of a recorded live run (no model called, cost $0.00); "
                  f"{result['cases']} cases, {len(result['failures'])} held after failures, {len(result['drafts'])} drafts")
            print(result["report"] or result["report_problems"][0])
            return
        if not args.scripted:
            budget, guess = clamp_budget(args.budget), estimate_close(conn if args.db.exists() else None)
            print(f"live model {args.effort} effort: estimated ${guess['usd']} ({guess['basis']}); spending cap ${budget}")
            if not args.yes and (not sys.stdin.isatty() or input("Start the live close? [y/N] ").strip().lower() != "y"):
                print("not started" + ("" if sys.stdin.isatty() else " (not a terminal: pass --yes to start)"))
                sys.exit(2)
        if args.scripted:
            policy = ScriptedPolicy()
        elif args.record:
            from vouch.agent.policy import recording_policy
            policy = recording_policy(args.record, effort=args.effort, budget_usd=budget)
        else:
            policy = ModelPolicy(effort=args.effort, budget_usd=budget)
        result = run_close(conn, policy)
        label = "SCRIPTED run (fast-test playbook, not the model)" if args.scripted else f"model {result['model']}"
        print(f"{result['run_id']}: {label}; {result['cases']} cases, {len(result['failures'])} held after failures, "
              f"{len(result['drafts'])} drafts, {len(result['requests'])} approval requests, cost ${result['cost_usd']}")
        if not result["report"]:
            print(f"{result['report_problems'][0]}; run the close again to finish it")
            return
        print(f"report {'accepted' if result['report_ok'] else 'REJECTED'} by the claim check"
              + ("" if result["report_ok"] else f": {json.dumps(result['report_problems'])}"))
        print(result["report"])


if __name__ == "__main__":
    main()
