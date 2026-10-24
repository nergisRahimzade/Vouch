"""G1: one command runs a full close with no human input until approvals (scripted policy,
labelled as such; the model policy runs the same checklist)."""
import json
import shutil
from decimal import Decimal as D

import pytest

from vouch.agent.policy import ScriptedPolicy
from vouch.agent.runner import run_close
from vouch.db import connect
from vouch.world import config as C


@pytest.fixture(scope="module")
def closed(db_path, tmp_path_factory):
    path = tmp_path_factory.mktemp("close") / "vouch.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    result = run_close(conn, ScriptedPolicy())
    yield conn, result
    conn.close()


def test_the_checklist_runs_in_order(closed):
    conn, result = closed
    steps = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE action LIKE 'close_step_%' ORDER BY seq")]
    assert steps == [f"close_step_{n}" for n in range(1, 8)]
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'close_report'").fetchone()[0] == 1
    assert result["mode"] == "scripted" and result["model"] is None and result["cost_usd"] == "0.0000"


def test_every_case_is_worked_and_none_left_open(closed):
    conn, result = closed
    statuses = [r[0] for r in conn.execute("SELECT status FROM cases")]
    assert len(statuses) == result["cases"] == 14 and "open" not in statuses and not result["failures"]


def test_entries_are_drafted_and_every_draft_waits_for_approval(closed):
    conn, result = closed
    drafts = [r[0] for r in conn.execute("SELECT entry_id FROM journal_entries WHERE origin = 'vouch'")]
    assert {f"VCH-{C.OPEN_MONTH}-{e}-{k}" for e in ("US", "UK", "DE") for k in ("PAYROLL", "STOCKCOMP")} <= set(drafts)
    assert len([d for d in drafts if d.startswith("VCH-F-")]) == 4                # the four corrections
    for entry_id in drafts:
        request = conn.execute("SELECT status FROM approval_requests WHERE kind = 'entry' AND subject_ref = ?",
                               (entry_id,)).fetchone()
        assert request and request[0] == "pending", entry_id


def test_nothing_is_approved_posted_released_or_reopened_without_a_person(closed):
    conn, _ = closed
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin = 'vouch' AND status = 'posted'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM approval_requests WHERE status <> 'pending'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM payment_releases").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM period_events WHERE action = 'reopen'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM holds WHERE released_at IS NOT NULL").fetchone()[0] == 0


def test_the_accrual_leaves_out_people_who_are_not_owed(closed):
    conn, _ = closed
    held = {json.loads(r[0])["subject"]["employee_id"] for r in conn.execute(
        "SELECT record FROM findings WHERE kind IN ('paid_not_in_hr', 'paid_after_exit', 'entity_differs_from_hr') "
        "AND period = ?", (C.OPEN_MONTH,))}
    assert len(held) == 3
    for entity in ("US", "UK", "DE"):
        register = sum(D(json.loads(r[0])["gross_pay"]) for r in conn.execute(
            "SELECT data FROM v_payroll_register WHERE month = ? AND entity = ?", (C.OPEN_MONTH, entity))
            if json.loads(r[0])["employee_id"] not in held)
        drafted = sum(D(r[0]) for r in conn.execute(
            "SELECT debit FROM journal_lines WHERE entry_id = ? AND account = '6000'", (f"VCH-{C.OPEN_MONTH}-{entity}-PAYROLL",)))
        assert drafted == register, entity


def test_stock_entries_name_their_rate_and_skip_unapproved_grants(closed):
    conn, _ = closed
    for entity in ("UK", "DE"):
        lines = conn.execute("SELECT fx_rate, fx_basis FROM journal_lines WHERE entry_id = ?",
                             (f"VCH-{C.OPEN_MONTH}-{entity}-STOCKCOMP",)).fetchall()
        assert lines and all(r[0] and C.OPEN_MONTH in r[1] for r in lines)


def test_the_report_passes_the_claim_check(closed):
    _, result = closed
    assert result["report_ok"] and result["report"].startswith(f"Close of {C.OPEN_MONTH} (scripted run)")


def test_running_the_close_twice_duplicates_nothing(closed):
    conn, _ = closed
    count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    before = {t: count(t) for t in ("cases", "holds", "questions", "approval_requests", "journal_entries")}
    second = run_close(conn, ScriptedPolicy())
    assert {t: count(t) for t in before} == before and second["run_id"] == "RUN-002"
