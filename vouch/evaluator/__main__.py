"""python -m vouch.evaluator score [--set main|heldout|clean|all] [--runs N] [--scripted] [--effort E] [--seed N]

Runs the real model by default. --scripted is the fast-test playbook; its reports say so.
Each set prints its own score: main (the planted mistakes), heldout (mistakes never used for
tuning) and clean (no mistakes)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vouch.evaluator import run


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m vouch.evaluator")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("score", help="run the agent on a fresh copy of the company and score it against the key")
    s.add_argument("--set", dest="sets", default="all", choices=[*run.SETS, "all"])
    s.add_argument("--runs", type=int, default=1)
    s.add_argument("--scripted", action="store_true", help="fixed playbook instead of the model (fast tests only)")
    s.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--seed", type=int, default=None, help="default: the generator's fixed seed")
    s.add_argument("--heldout", type=Path, default=run.HELDOUT_DIR, help="directory of the held-out plants")
    s.add_argument("--results", type=Path, default=run.DEFAULT_RESULTS)
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.command == "score":
        sets = run.SETS if args.sets == "all" else (args.sets,)
        for set_name in sets:
            try:
                report = run.score(set_name, args.runs, "scripted" if args.scripted else "model", args.seed,
                                   args.results, effort=args.effort, heldout_dir=args.heldout)
            except run.NoModelAccess as missing:
                print(f"not scored: {missing}")
                sys.exit(2)
            print(run.render(report))
            print(f"  report saved: {args.results / report['report_file']}\n")


if __name__ == "__main__":
    main()
