"""python -m vouch.controls inbox --role ROLE
   python -m vouch.controls decide REQUEST --as ROLE (approve|reject) --note TEXT [--callback]
   python -m vouch.controls verify"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vouch.controls import approvals, audit, gates
from vouch.db import DEFAULT_DB, connect
from vouch.db.periods import directory_actor


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vouch.controls")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    i = sub.add_parser("inbox", help="requests waiting for a role")
    i.add_argument("--role", required=True)
    d = sub.add_parser("decide", help="approve or reject a request as a person in a role")
    d.add_argument("request_id")
    d.add_argument("--as", dest="role", required=True)
    d.add_argument("decision", choices=["approve", "reject"])
    d.add_argument("--note", required=True)
    d.add_argument("--callback", action="store_true", help="HR: the call-back to the number on file was made")
    sub.add_parser("verify", help="check the audit trail's hash chain")
    args = parser.parse_args()

    conn = connect(args.db)
    gates.ensure(conn)
    if args.command == "inbox":
        for item in approvals.inbox(conn, args.role):
            print(f"{item['request_id']}  {item['kind']:<16} {item['subject_ref']:<28} "
                  f"{item['amount'] or '':>12} {item['currency'] or '':<4} from {item['requested_by']} "
                  f"({item['status']}, step {len(item['approvals']) + 1} of {len(item['needs'])})")
    elif args.command == "decide":
        actor = directory_actor(conn, args.role)
        try:
            result = approvals.decide(conn, actor, args.request_id, args.decision, args.note, args.callback)
        except gates.Refused as refusal:
            print(f"refused ({refusal.rule}): {refusal.reason}")
            sys.exit(1)
        conn.commit()
        print(json.dumps({k: result[k] for k in ("request_id", "status", "assigned_role")}))
    elif args.command == "verify":
        result = audit.verify(conn)
        print(f"audit trail {'intact' if result.ok else 'BROKEN'}: {result.rows} rows checked"
              + ("" if result.ok else f"; first bad row {result.first_bad_seq}: {result.problem}"))
        sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
