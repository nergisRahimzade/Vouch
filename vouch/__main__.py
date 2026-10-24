"""python -m vouch rebuild — one command: build the world from the seed, then load it."""
from __future__ import annotations

import argparse

from vouch.db.rebuild import rebuild, report
from vouch.world.build import DEFAULT_ROOT, build


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vouch")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("rebuild", help="generate the world and rebuild the database")
    args = parser.parse_args()
    if args.command == "rebuild":
        world = build(DEFAULT_ROOT)
        print(f"world: {world['files']} files, {world['plants']} planted mistakes, {world['noise']} noise items")
        print(report(rebuild(DEFAULT_ROOT)))


if __name__ == "__main__":
    main()
