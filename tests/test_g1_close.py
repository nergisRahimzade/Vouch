"""G1: public trigger flow produces only drafts and can resume without duplicate artifacts."""
from vouch.agent.runner import run_scenario


def test_scripted_run_creates_drafts_and_waits_for_people(public_case):
    conn,s=public_case("P01");r=run_scenario(conn,s)
    assert r["mode"]=="scripted" and r["metrics"]["llm_calls"]==0
    assert r["decisions"][0]["wage_taxable"]=="6291.60"
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin='vouch' AND status='posted'").fetchone()[0]==0
    assert conn.execute("SELECT COUNT(*) FROM payroll_exports WHERE origin='vouch' AND status<>'draft'").fetchone()[0]==0
    before=conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
    run_scenario(conn,s)
    assert conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]==before
    assert conn.execute("SELECT COUNT(*) FROM payroll_exports WHERE origin='vouch'").fetchone()[0]==60
