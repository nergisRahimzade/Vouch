"""G8/T10: scripted colleagues supply facts; tax-manager rulings persist and are reused."""
from vouch.agent.runner import run_scenario
from vouch.agent import questions


def test_scripted_vendor_and_organizer_replies_complete_cases(public_case):
    for case_id in ("P02","P06","P07"):
        conn,s=public_case(case_id);d=run_scenario(conn,s)["decisions"][0]
        assert d["decision"]=="classify_and_book"
        assert conn.execute("SELECT COUNT(*) FROM received_facts").fetchone()[0]==1


def test_tax_manager_answer_is_followed_without_reasking(public_case):
    conn,s=public_case("P10");first=run_scenario(conn,s)["decisions"][0]
    assert first["decision"]=="escalate"
    request=conn.execute("SELECT request_id,recipient FROM info_requests WHERE about='nonqualifying_event'").fetchone()
    questions.answer(conn,request[0],request[1],{"method":"individual","reason":"Use individual payroll taxation"})
    second=run_scenario(conn,s)["decisions"][0]
    assert second["payroll_method"]=="individual" and second["wage_taxable"]=="4165.00"
    assert conn.execute("SELECT COUNT(*) FROM learned_corrections").fetchone()[0]==1
    assert conn.execute("SELECT COUNT(*) FROM info_requests WHERE about='nonqualifying_event'").fetchone()[0]==1
