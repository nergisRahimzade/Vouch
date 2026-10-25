"""Before/after displays derive from real booked invoices, never scenario answer literals."""
from vouch.agent.runner import run_scenario
from vouch.agent.policy import ScriptedPolicy


def test_late_invoice_preserves_auditable_cost_transition(public_case):
    conn,scenario=public_case('P11')
    d=run_scenario(conn,scenario,ScriptedPolicy())['decisions'][0]
    t=d['cost_transition']
    assert t['before_per_participant']=='107.43'
    assert t['after_per_participant']=='111.40'
    assert t['before_gross']=='38675.00' and t['after_gross']=='40103.00'
    assert t['participants']==360
    assert t['vat_before_recoverable'] and not t['vat_after_recoverable']
    assert t['new_invoice_ids']==['INV-FW-0910']


def test_new_event_has_no_invented_previous_cost(public_case):
    conn,scenario=public_case('P01')
    d=run_scenario(conn,scenario,ScriptedPolicy())['decisions'][0]
    assert 'cost_transition' not in d
