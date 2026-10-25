"""python -m vouch.world build [--case FILE] [--as-of YYYY-MM-DD]
Shows what the company world holds on a date: the base fixtures, or one scenario on top of them."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vouch.world.build import build, summary
from vouch.world.scenario import load_file


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vouch.world")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="show the company world on a date")
    b.add_argument("--case", type=Path, default=None, help="a scenario YAML file (its first scenario)")
    b.add_argument("--as-of", default=None)
    args = parser.parse_args()
    scenario = load_file(args.case)[0] if args.case else None
    print(json.dumps(summary(*build(scenario, args.as_of)), indent=1))


if __name__ == "__main__":
    main()
