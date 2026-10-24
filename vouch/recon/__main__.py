"""python -m vouch.recon run [--db PATH] — reconcile every source for the open month's close."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from vouch.db import DEFAULT_DB, connect
from vouch.recon.run import reconcile


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vouch.recon")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run every reconciliation check and store the findings")
    r.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = connect(args.db)
    findings = reconcile(conn)
    cases, explained = findings.cases(), findings.explained()
    print(f"{len(cases)} cases, {len(explained)} explained differences")
    for kind, n in sorted(Counter(f.kind for f in cases).items()):
        print(f"  case       {kind}: {n}")
    for kind, n in sorted(Counter(f.kind for f in explained).items()):
        print(f"  explained  {kind}: {n}")


if __name__ == "__main__":
    main()
