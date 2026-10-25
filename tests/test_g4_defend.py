"""G4: defenses use only the case audit evidence and reject invented numbers."""
from vouch.agent.defend import why
from vouch.agent.policy import ScriptedPolicy
from vouch.agent.runner import run_scenario


def test_case_defense_cites_audit_records(public_case):
    conn,s=public_case("P03");run_scenario(conn,s)
    r=why(conn,s.tasks[0]["event"],"Why?",ScriptedPolicy())
    assert r["ok"] and r["rows"] and r["answer"]


def test_unsupported_claim_is_refused(public_case):
    conn,s=public_case("P01");run_scenario(conn,s)
    class Inventor(ScriptedPolicy):
        def defend(self,*args): return "We recovered 999999.99 under R-INVENTED."
    r=why(conn,s.tasks[0]["event"],"Why?",Inventor())
    assert not r["ok"] and not r["answer"] and r["problems"]
