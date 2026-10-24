"""python -m vouch.db rebuild [--world DIR] [--db PATH]"""
from __future__ import annotations

import argparse
from pathlib import Path

from vouch.db import DEFAULT_DB
from vouch.db.rebuild import DEFAULT_WORLD, rebuild, report


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vouch.db")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("rebuild", help="load the world into a fresh database")
    r.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    r.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if args.command == "rebuild":
        print(report(rebuild(args.world, args.db)))


if __name__ == "__main__":
    main()
