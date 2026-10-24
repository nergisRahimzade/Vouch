"""python -m vouch.evaluator score [--set main|heldout|clean|all] [--runs N] [--scripted] [--effort E] [--seed N]
   python -m vouch.evaluator score --demo     the demo scenario: the main set, five runs, all at target in a row

Runs the real model by default. --scripted is the fast-test playbook; its reports say so.
Each set prints its own score: main (the planted mistakes), heldout (mistakes never used for
tuning) and clean (no mistakes). A score rests on at least three runs; a planted mistake counts
as caught only if every run caught it."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vouch.agent.cost import DEFAULT_BUDGET_USD, clamp_budget, estimate_close, estimate_score
from vouch.evaluator import run


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m vouch.evaluator")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("score", help="run the agent on a fresh copy of the company and score it against the key")
    s.add_argument("--set", dest="sets", default="all", choices=[*run.SETS, "all"])
    s.add_argument("--runs", type=int, default=run.MIN_RUNS, help=f"at least {run.MIN_RUNS}")
    s.add_argument("--demo", action="store_true", help=f"the main set, {run.DEMO_RUNS} runs in a row at target")
    s.add_argument("--scripted", action="store_true", help="fixed playbook instead of the model (fast tests only)")
    s.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--yes", action="store_true", help="start the live model without asking first")
    s.add_argument("--budget", default=None, help="spending cap in USD for each run (default: the agent's default cap)")
    s.add_argument("--seed", type=int, default=None, help="default: the generator's fixed seed")
    s.add_argument("--heldout", type=Path, default=run.HELDOUT_DIR, help="directory of the held-out plants")
    s.add_argument("--results", type=Path, default=run.DEFAULT_RESULTS)
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.command == "score":
        if args.runs < run.MIN_RUNS:
            print(f"not scored: a score rests on at least {run.MIN_RUNS} runs (got --runs {args.runs})")
            sys.exit(2)
        sets, runs = (("main",), max(args.runs, run.DEMO_RUNS)) if args.demo else \
            (run.SETS if args.sets == "all" else (args.sets,), args.runs)
        if not args.scripted:
            try:
                run.policy_factory("model", args.effort)             # no key: say so before asking about cost
            except run.NoModelAccess as missing:
                print(f"not scored: {missing}")
                sys.exit(2)
            scorable = [x for x in sets if not run.unavailable(x, args.heldout)]
            guess = estimate_score(None, runs * len(scorable))
            print(f"live model: {len(scorable)} set(s) x {runs} runs = {runs * len(scorable)} full closes, "
                  f"estimated ${guess['usd']} ({estimate_close(None)['basis']}); "
                  f"cap ${args.budget or DEFAULT_BUDGET_USD} per run")
            if not args.yes and (not sys.stdin.isatty() or input("Start the live score? [y/N] ").strip().lower() != "y"):
                print("not started" + ("" if sys.stdin.isatty() else " (not a terminal: pass --yes to start)"))
                sys.exit(2)
        verdicts = []
        for set_name in sets:
            try:
                report = run.score(set_name, runs, "scripted" if args.scripted else "model", args.seed,
                                   args.results, effort=args.effort, heldout_dir=args.heldout,
                                   budget_usd=None if args.budget is None else clamp_budget(args.budget))
            except run.NoModelAccess as missing:
                print(f"not scored: {missing}")
                sys.exit(2)
            print(run.render(report))
            print(f"  report saved: {args.results / report['report_file']}\n")
            verdicts.append(report.get("demo", {}).get("passed"))
        if args.demo:
            sys.exit(0 if verdicts[0] else 1)


if __name__ == "__main__":
    main()
