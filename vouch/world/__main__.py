"""python -m vouch.world build [--out DIR] [--seed N]"""
from __future__ import annotations

import argparse
from pathlib import Path

from vouch.world import config as C
from vouch.world.build import DEFAULT_ROOT, build


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vouch.world")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="generate every file of the company world from the seed")
    b.add_argument("--out", type=Path, default=DEFAULT_ROOT)
    b.add_argument("--seed", type=int, default=C.SEED)
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.out, args.seed)
        print(f"world built at {result['root']} (seed {result['seed']}): {result['files']} files, "
              f"{result['people']} people ever employed, months {result['months'][0]}..{result['months'][-1]}")


if __name__ == "__main__":
    main()
