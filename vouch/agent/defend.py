"""Defending a decision. Asked why it did something, Vouch answers from the audit trail of that case
only: the data, rule, document, person and date recorded there. The claim check rejects any
answer that cites something the trail does not contain."""
from __future__ import annotations

import json
import sqlite3

from vouch.agent import claims
from vouch.controls import audit
from vouch.controls.gates import VOUCH


def evidence_for(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    return audit.trail(conn, case_id)


def _names(conn) -> list[str]:
    return [p[0] for p in conn.execute("SELECT name FROM people")]


def why(conn: sqlite3.Connection, case_id: str, question: str, policy) -> dict:
    rows = evidence_for(conn, case_id)
    if not rows:
        return {"case_id": case_id, "answer": f"The audit trail has no record of {case_id}.", "ok": True,
                "problems": [], "rows": []}
    corpus = [json.dumps(r, default=str, ensure_ascii=False) for r in rows]
    answer = policy.defend(question, rows)
    check = claims.check(answer, corpus, _names(conn))
    if not check.ok:
        answer = policy.defend(question, rows, check.problems)
        check = claims.check(answer, corpus, _names(conn))
    audit.log(conn, VOUCH, "defend", case_id, "done" if check.ok else "refused",
              "answer drawn from the audit trail" if check.ok else "answer rejected by the claim check",
              {"question": question, "rows": [r["seq"] for r in rows], "problems": check.problems})
    conn.commit()
    return {"case_id": case_id, "answer": answer if check.ok else "", "ok": check.ok, "problems": check.problems,
            "rows": [r["seq"] for r in rows]}
