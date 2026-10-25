"""Speed, Phase 3: several cases are worked at once. Each case must be worked exactly as it would be alone: the
same holds, questions, requests and drafts, numbered without clashes, with an intact audit chain, and each case's
tool errors counted against that case only."""
import shutil
import threading
import time

from vouch.agent.policy import ScriptedPolicy
from vouch.agent.runner import run_close
from vouch.controls import audit
from vouch.db import connect


def _decisions(conn):
    out = {}
    for (cid,) in conn.execute("SELECT case_id FROM cases ORDER BY case_id").fetchall():
        out[cid] = (
            conn.execute("SELECT status FROM cases WHERE case_id = ?", (cid,)).fetchone()[0],
            sorted((r[0], r[1]) for r in conn.execute("SELECT target_kind, target_ref FROM holds WHERE case_id = ?", (cid,))),
            sorted((r[0], r[1], r[2], r[3]) for r in conn.execute(
                "SELECT to_role, if_yes, if_no, if_partial FROM questions WHERE case_id = ?", (cid,))),
            sorted((r[0], r[1], r[2]) for r in conn.execute(
                "SELECT kind, subject_ref, amount FROM approval_requests WHERE case_id = ?", (cid,))),
            sorted(tuple(r) for r in conn.execute(
                "SELECT account, department, debit, credit FROM journal_lines WHERE entry_id = ?", (f"VCH-{cid}",))))
    return out


def _close(db_path, tmp_path, name, policy):
    path = tmp_path / name
    shutil.copyfile(db_path, path)
    conn = connect(path)
    result = run_close(conn, policy)
    return conn, result


class _SlowScripted(ScriptedPolicy):
    """The playbook, with a pause before each case, so cases really overlap."""
    def __init__(self, workers):
        super().__init__(parallel_cases=workers)
        self.overlap, self._busy, self._lock = 0, 0, threading.Lock()

    def work(self, case, toolbox):
        with self._lock:
            self._busy += 1
            self.overlap = max(self.overlap, self._busy)
        time.sleep(0.05)
        try:
            return super().work(case, toolbox)
        finally:
            with self._lock:
                self._busy -= 1


def test_cases_worked_at_once_are_decided_exactly_as_one_at_a_time(db_path, tmp_path):
    one, r1 = _close(db_path, tmp_path, "one.db", _SlowScripted(1))
    policy = _SlowScripted(4)
    many, r4 = _close(db_path, tmp_path, "many.db", policy)
    try:
        assert policy.overlap > 1                                       # they really ran at the same time
        assert _decisions(one) == _decisions(many)
        assert r1["failures"] == r4["failures"] == {}
        assert len(r1["requests"]) == len(r4["requests"])               # numbers follow finishing order; contents
                                                                        # are compared in _decisions
        for conn in (one, many):
            assert audit.verify(conn).ok
            ids = [r[0] for r in conn.execute("SELECT request_id FROM approval_requests")]
            assert len(ids) == len(set(ids))                            # no clashing numbers
            qs = [r[0] for r in conn.execute("SELECT question_id FROM questions")]
            assert len(qs) == len(set(qs))
        assert r4["report_ok"]
    finally:
        one.close()
        many.close()


class _BrokenToolForOneKind(ScriptedPolicy):
    """One kind of case hits a tool that breaks; the others are fine. Only that case may be held for it."""
    def __init__(self, kind):
        super().__init__(parallel_cases=4)
        self.kind = kind

    def work(self, case, toolbox):
        if case["kind"] == self.kind:
            toolbox.call("get_bank", {"reference": "ANY"})               # broken below, for this case only
        return super().work(case, toolbox)


def test_a_tool_error_is_counted_against_its_own_case_only(db_path, tmp_path, monkeypatch):
    from vouch.agent.tools import Toolbox

    def broken(self, reference):
        raise RuntimeError("the bank feed is down")
    monkeypatch.setattr(Toolbox, "t_get_bank", broken)
    conn, result = _close(db_path, tmp_path, "w.db", _BrokenToolForOneKind("paid_after_exit"))
    try:
        assert result["failures"]
        kinds = {conn.execute("SELECT kind FROM cases WHERE case_id = ?", (c,)).fetchone()[0]
                 for c in result["failures"]}
        assert kinds == {"paid_after_exit"}
        assert all("bank feed is down" in reason for reason in result["failures"].values())
    finally:
        conn.close()
