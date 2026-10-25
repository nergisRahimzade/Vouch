"""Work one kind of case with the live model on a throwaway copy of the database, and show what was decided and
what it cost. For checking a change on the cases it touches, for about a dime, without a full close or score.
The copy is deleted afterwards; the real database is never written."""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from vouch.agent import runner
from vouch.agent.policy import ModelPolicy
from vouch.agent.tools import Toolbox
from vouch.controls import gates
from vouch.controls.gates import VOUCH
from vouch.db import connect
from vouch.recon.run import reconcile


def try_case(db: Path, kind: str, budget_usd, policy=None) -> dict:
    with tempfile.TemporaryDirectory(prefix="vouch-try-") as work:
        copy = Path(work) / "copy.db"
        source = connect(db)
        target = connect(copy)
        source.backup(target)
        source.close()
        conn = target
        try:
            runner.ensure(conn)
            for table in ("cases", "holds", "questions", "approval_requests"):
                conn.execute(f"DELETE FROM {table}")       # a fresh start for the case on the copy only
            cases = [f.to_record() for f in reconcile(conn).cases()]
            case = next((c for c in cases if c["kind"] == kind), None)
            if case is None:
                return {"kind": kind, "error": f"no {kind} case in this company; kinds here: "
                                               f"{sorted({c['kind'] for c in cases})}"}
            gates.open_case(conn, VOUCH, case["finding_id"], case["period"], case["kind"], case["summary"], case)
            toolbox, policy = Toolbox(conn), policy or ModelPolicy(budget_usd=budget_usd, parallel_cases=1)
            started = time.time()
            explanation = policy.work(toolbox.cases[case["finding_id"]], toolbox)
            cid = case["finding_id"]
            return {
                "kind": kind, "case_id": cid, "seconds": round(time.time() - started, 1), "mode": policy.mode,
                "model": policy.model, "cost_usd": str(policy.usage.cost(policy.model or "")),
                "status": conn.execute("SELECT status FROM cases WHERE case_id = ?", (cid,)).fetchone()[0],
                "holds": [f"{r[0]}:{r[1]}" for r in conn.execute(
                    "SELECT target_kind, target_ref FROM holds WHERE case_id = ?", (cid,))],
                "questions": [f"{r[0]}: yes->{r[1]} no->{r[2]} partial->{r[3]}" for r in conn.execute(
                    "SELECT to_role, if_yes, if_no, if_partial FROM questions WHERE case_id = ?", (cid,))],
                "requests": [f"{r[0]} -> {r[1]}" for r in conn.execute(
                    "SELECT kind, assigned_role FROM approval_requests WHERE case_id = ?", (cid,))],
                "draft": bool(conn.execute("SELECT 1 FROM journal_entries WHERE entry_id = ?", (f"VCH-{cid}",)).fetchone()),
                "explanation": explanation}
        finally:
            conn.close()
