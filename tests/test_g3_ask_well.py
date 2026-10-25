"""G3: attributed structured answers, partial evidence, and immutable source receipts."""
import pytest
from vouch.agent import questions
from vouch.agent.tools import Toolbox
from vouch.recon.run import prepass


def test_only_requested_person_can_answer_and_partial_answer_stays_held(public_case):
    conn,s=public_case("P02");event=s.tasks[0]["event"]
    box=Toolbox(conn,s.tasks[0]);box.refresh()
    request=box.candidate["requests"][0]
    rid=questions.request_info(conn,event,request["to"],request["about"])
    with pytest.raises(PermissionError): questions.answer(conn,rid,"P-MKT",{"participants":["BP-001"]})
    questions.answer(conn,rid,request["to"],{"participants":["BP-001"]})
    assert prepass(conn,s.tasks[0])["decision"]["decision"]=="hold"
    assert conn.execute("SELECT participants FROM invoices WHERE id='INV-RST-0611'").fetchone()[0]=="[]"


def test_package_reply_must_reconcile_to_the_original_invoice(public_case):
    conn,s=public_case("P07");event=s.tasks[0]["event"]
    rid=questions.request_info(conn,event,"V-AGENTUR","itemized_invoice")
    with pytest.raises(ValueError): questions.answer(conn,rid,"V-AGENTUR",{"lines":[{"net":"1","vat":"0","gross":"2"}]})


def test_prose_instructions_are_not_typed_response_fields(public_case):
    conn,s=public_case("P02");event=s.tasks[0]["event"]
    rid=questions.request_info(conn,event,"P-SALES-HEAD","bewirtung_attendees_occasion")
    with pytest.raises(ValueError): questions.answer(conn,rid,"P-SALES-HEAD",{"approve":True})
