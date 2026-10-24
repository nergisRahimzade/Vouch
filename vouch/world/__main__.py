"""python -m vouch.world build [--out DIR] [--seed N]"""
from __future__ import annotations

import argparse
from pathlib import Path

from vouch.world import config as C
from vouch.world.build import DEFAULT_ROOT, build
from vouch.world.heldout import DEFAULT_DIR as HELDOUT_DIR


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vouch.world")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="generate every file of the company world from the seed")
    b.add_argument("--out", type=Path, default=DEFAULT_ROOT)
    b.add_argument("--seed", type=int, default=C.SEED)
    b.add_argument("--clean", action="store_true", help="the clean set: noise but no planted mistakes")
    b.add_argument("--heldout", type=Path, nargs="?", const=HELDOUT_DIR, default=None,
                   help="the held-out set: apply the held-out plants (default directory ./heldout) to the open month")
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.out, args.seed, clean=args.clean, heldout_dir=args.heldout)
        print(f"world built at {result['root']} (seed {result['seed']}): {result['files']} files, "
              f"{result['people']} people ever employed, months {result['months'][0]}..{result['months'][-1]}")
        print(f"set {result['set']}: {result['plants']} planted mistakes and {result['noise']} noise items; answer key sealed at "
              f"{result['answer_key']} ({result['pending_hand_checks']} amounts await an independent hand check)")


if __name__ == "__main__":
    main()
