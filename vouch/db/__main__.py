"""python -m vouch.db rebuild [--db PATH] [--case FILE] [--as-of YYYY-MM-DD]"""
from __future__ import annotations

import argparse
from pathlib import Path

from vouch.db import DEFAULT_DB
from vouch.db.rebuild import rebuild, report
from vouch.world.scenario import load_file


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vouch.db")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("rebuild", help="load the company world into a fresh database")
    r.add_argument("--db", type=Path, default=DEFAULT_DB)
    r.add_argument("--case", type=Path, default=None, help="a scenario YAML file (its first scenario)")
    r.add_argument("--as-of", default=None)
    args = parser.parse_args()
    if args.command == "rebuild":
        scenario = load_file(args.case)[0] if args.case else None
        print(report(rebuild(args.db, scenario, args.as_of)))


if __name__ == "__main__":
    main()
