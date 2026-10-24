"""python -m vouch rebuild              build the world from the seed and load it
   python -m vouch close [--scripted]   run the open month's close (the real model by default)"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

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
    args = parser.parse_args()
    if args.command == "rebuild":
        world = build(DEFAULT_ROOT)
        print(f"world: {world['files']} files, {world['plants']} planted mistakes, {world['noise']} noise items")
        print(report(rebuild(DEFAULT_ROOT, args.db)))
    elif args.command == "close":
        from vouch.agent.policy import ModelPolicy, ScriptedPolicy
        from vouch.agent.runner import run_close
        policy = ScriptedPolicy() if args.scripted else ModelPolicy(effort=args.effort)
        result = run_close(connect(args.db), policy)
        label = "SCRIPTED run (fast-test playbook, not the model)" if args.scripted else f"model {result['model']}"
        print(f"{result['run_id']}: {label}; {result['cases']} cases, {len(result['failures'])} held after failures, "
              f"{len(result['drafts'])} drafts, {len(result['requests'])} approval requests, cost ${result['cost_usd']}")
        print(f"report {'accepted' if result['report_ok'] else 'REJECTED'} by the claim check"
              + ("" if result["report_ok"] else f": {json.dumps(result['report_problems'])}"))
        print(result["report"])


if __name__ == "__main__":
    main()
