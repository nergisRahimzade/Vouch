"""G6: when the model cannot write the close report (unreachable, timed out, refused), the close ends as a
failed run with a plain reason and its real cost. No report is invented, nothing done is lost, and running
the close again finishes it."""
import shutil

from vouch.agent.policy import PolicyFailed, ScriptedPolicy
from vouch.agent.runner import run_close
from vouch.controls import audit
from vouch.db import connect


class _ReportUnreachable(ScriptedPolicy):
    def report(self, facts, problems=None):
        raise PolicyFailed("the model API could not be reached")


def test_a_report_that_cannot_be_written_ends_the_run_as_failed_with_its_reason(db_path, tmp_path):
    path = tmp_path / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    try:
        result = run_close(conn, _ReportUnreachable())
        assert not result["report_ok"] and result["report"] == ""
        assert result["report_problems"] == ["report not written: the model API could not be reached"]
        run = conn.execute("SELECT status, report, report_status, finished_at FROM agent_runs").fetchone()
        assert run["status"] == "failed" and run["report"] is None and run["report_status"] == "not written"
        assert run["finished_at"]
        logged = conn.execute("SELECT outcome, reason FROM audit_log WHERE action = 'close_report'").fetchone()
        assert logged["outcome"] == "failed" and "could not be reached" in logged["reason"]
        drafts = conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin = 'vouch'").fetchone()[0]
        assert drafts >= len(result["drafts"]) > 0                      # the work done so far is kept

        again = run_close(conn, ScriptedPolicy())                       # running it again finishes the close
        assert again["report_ok"] and again["report"]
        assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin = 'vouch'").fetchone()[0] == drafts
        assert audit.verify(conn).ok
    finally:
        conn.close()
