"""Generalized boundary checks independent of public scenario answer blocks."""
from decimal import Decimal as D
from vouch.agent.runner import ensure,run_scenario
from vouch.calc.eventtax import Rules
from vouch.calc.ledgers import EventAttribution,optimize_allowances
from vouch.recon.run import prepass,benefit_totals
from vouch.recon.sources import Sources
from vouch.world.scenario import rows_for


def test_allowances_restart_each_calendar_year():
    rules=Rules(rows_for()[0]["rules"])
    attrs=[EventAttribution(f"EVENT-{year}-{n}",f"{year}-0{n}-01",True,{"person":D("100")})
           for year in (2025,2026) for n in (1,2)]
    result=optimize_allowances(attrs,rules)
    assert len(result["person"])==4


def test_delivery_date_wins_over_conflicting_text(scenario_db):
    raw={"id":"reference-conflict","as_of":"2026-10-05","setup":{
      "events":[{"id":"EVENT-OLD","name":"Old gathering","date":"2026-08-01","open_to":"all"},
                {"id":"EVENT-NEW","name":"New gathering","date":"2026-10-01","open_to":"all","type_declared":"staff_event"}],
      "attendance":[{"event":"EVENT-NEW","employees":"E001-E002"}],
      "invoices":[{"id":"BILL-REFERENCE","event_ref":"Old gathering","delivery_date":"2026-10-01","received_at":"2026-10-02",
                   "lines":[{"category":"catering","net":"100","vat":"19","gross":"119","vat_rate":"0.19"}]}]}}
    conn=scenario_db(raw);ensure(conn)
    d=prepass(conn,{"type":"process_event","event":"EVENT-NEW"})["decision"]
    assert d["matched_event"]=="EVENT-NEW" and "event_reference_mismatch" in d["flags"]


def test_cap_combines_program_and_gift_benefits(scenario_db):
    raw={"id":"combined-benefits","as_of":"2026-10-05","setup":{
      "events":[{"id":"EVENT-PROGRAM","date":"2026-01-20","open_to":"all","type_declared":"mixed"},
                {"id":"EVENT-GIFT","date":"2026-10-01","type_declared":"gift"}],
      "attendance":[{"event":"EVENT-PROGRAM","employees":"E001","business_partners":"BP-001"}],
      "invoices":[{"id":"BILL-PROGRAM","event":"EVENT-PROGRAM","received_at":"2026-01-20","delivery_date":"2026-01-20",
                   "lines":[{"category":"program","net":"9000","vat":"1710","gross":"10710"}]},
                  {"id":"BILL-GIFT","event":"EVENT-GIFT","received_at":"2026-10-02","delivery_date":"2026-10-01",
                   "lines":[{"category":"gift","net":"4000","vat":"760","gross":"4760","qty":1,"unit_net":"4000","recipient":"BP-001"}]}]}}
    conn=scenario_db(raw);ensure(conn)
    assert benefit_totals(Sources(conn),2026)["BP-001"]==D("10115")
    d=prepass(conn,{"type":"process_event","event":"EVENT-GIFT"})["decision"]
    assert d["decision"]=="escalate" and "flat_tax_cap_exceeded" in d["flags"]


def test_clean_case_uses_standing_policy_not_an_unrequested_human_approval(public_case):
    conn,s=public_case("C01");run_scenario(conn,s)
    rows=list(conn.execute("SELECT status,assigned_role FROM approval_requests"))
    assert rows and all(tuple(r)==("approved","policy") for r in rows)
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='approve' AND role='vouch'").fetchone()[0]==0


def test_partial_documentation_never_books_hospitality(public_case):
    conn,s=public_case("P02")
    s.people_responses=[]
    d=run_scenario(conn,s)["decisions"][0]
    assert d["decision"]=="hold"
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin='vouch'").fetchone()[0]==0


def test_partial_reply_can_be_completed_without_changing_original_receipt(public_case):
    from vouch.agent import questions
    conn,s=public_case("P02")
    rid=questions.request_info(conn,s.tasks[0]["event"],"P-SALES-HEAD","bewirtung_attendees_occasion")
    questions.answer(conn,rid,"P-SALES-HEAD",{"participants":["BP-001","BP-002","BP-003","BP-004","E201","E202"]})
    assert prepass(conn,s.tasks[0])["decision"]["decision"]=="hold"
    questions.answer(conn,rid,"P-SALES-HEAD",{"occasion":"Project completion for production line four"})
    assert prepass(conn,s.tasks[0])["decision"]["decision"]=="classify_and_book"
    assert conn.execute("SELECT participants FROM invoices WHERE id='INV-RST-0611'").fetchone()[0]=="[]"


def test_attendance_answer_supplies_actual_rows(scenario_db):
    from vouch.agent import questions
    raw={"id":"attendance-response","as_of":"2026-10-05","setup":{
        "events":[{"id":"EVENT-ATTENDANCE","date":"2026-10-01","open_to":"all","type_declared":"staff_event","organizer":"P-HR-EVT"}],
        "invoices":[{"id":"BILL-ATTENDANCE","event":"EVENT-ATTENDANCE","received_at":"2026-10-02","delivery_date":"2026-10-01",
                     "lines":[{"category":"catering","net":"100","vat":"19","gross":"119"}]}]}}
    conn=scenario_db(raw);ensure(conn)
    task={"type":"process_event","event":"EVENT-ATTENDANCE"}
    assert prepass(conn,task)["decision"]["decision"]=="hold"
    rid=questions.request_info(conn,"EVENT-ATTENDANCE","P-HR-EVT","attendance")
    questions.answer(conn,rid,"P-HR-EVT",{"employees":"E001-E002"})
    assert prepass(conn,task)["decision"]["per_participant"]=="59.50"


def test_first_gift_over_the_limit_requires_tax_review(scenario_db):
    raw={"id":"gift-over-limit","as_of":"2026-10-05","setup":{
      "events":[{"id":"EVENT-GIFT-LIMIT","date":"2026-10-01","type_declared":"gift"}],
      "invoices":[{"id":"BILL-GIFT-LIMIT","event":"EVENT-GIFT-LIMIT","received_at":"2026-10-02","delivery_date":"2026-10-01",
                   "lines":[{"category":"gift","net":"50.01","vat":"9.50","gross":"59.51","qty":1,"unit_net":"50.01","recipient":"BP-001"}]}]}}
    conn=scenario_db(raw);ensure(conn)
    d=prepass(conn,{"type":"process_event","event":"EVENT-GIFT-LIMIT"})["decision"]
    assert "gift_limit_exceeded" in d["flags"]
    assert d["approvers"]==["tax_manager"] and d["nondeductible"]=="59.51"


def test_email_trigger_resolves_its_referenced_invoice(public_case):
    conn,s=public_case("P09")
    s.tasks=[{"type":"process_email","email_id":"EM-ELECTION"}]
    d=run_scenario(conn,s)["decisions"][0]
    assert d["classification"]=="gift" and d["flat_tax_37b"]=="16.07"
    assert "election_change_request" in d["flags"]


def test_confirmed_unknown_gift_recipients_escalate_without_guessing(scenario_db):
    from vouch.world.scenario import parse
    raw={"id":"anonymous-gifts","as_of":"2026-10-05","setup":{
      "events":[{"id":"EVENT-ANONYMOUS","date":"2026-10-01","type_declared":"gift","organizer":"P-MKT"}],
      "invoices":[{"id":"BILL-ANONYMOUS","event":"EVENT-ANONYMOUS","received_at":"2026-10-02","delivery_date":"2026-10-01",
                   "lines":[{"category":"gift","net":"28.00","vat":"5.32","gross":"33.32","qty":2,"unit_net":"14.00"}]}]},
      "tasks":[{"type":"process_event","event":"EVENT-ANONYMOUS"}],
      "people_responses":[{"person":"P-MKT","about":"gift_recipient_list","data":{"recipients":["BP-001"],"unknown_count":1}}]}
    conn=scenario_db(raw);ensure(conn)
    d=run_scenario(conn,parse(raw))["decisions"][0]
    assert d["decision"]=="escalate" and d["escalations"]==["tax_manager"]
    assert not d.get("failure")
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin='vouch'").fetchone()[0]==0


def test_recorded_absence_routes_tax_review_to_the_delegate(scenario_db):
    from vouch.world.scenario import read_yaml,parse
    from vouch.controls import approvals
    from vouch.db.periods import person_actor
    raw=read_yaml(__import__("pathlib").Path(__file__).resolve().parents[1]/"cases"/"public"/"P01.yaml")
    raw["id"]="delegated-review"
    raw["overrides"]={"people":{"P-TAX":{"out_of_office_until":"2026-08-31"}}}
    conn=scenario_db(raw);ensure(conn)
    run_scenario(conn,parse(raw))
    requests=list(conn.execute("SELECT request_id,assigned_role FROM approval_requests WHERE status='pending'"))
    assert requests and all(r["assigned_role"]=="cfo" for r in requests)
    approvals.decide(conn,person_actor(conn,"cfo"),requests[0]["request_id"],"approve","Acting under the recorded delegation")
    assert approvals.get(conn,requests[0]["request_id"])["status"]=="approved"


def test_recovered_case_keeps_its_review_history_and_hold_until_approval(public_case):
    from vouch.agent import questions
    from vouch.controls import approvals,gates
    from vouch.db.periods import person_actor
    conn,s=public_case("P02")
    reply=s.people_responses[0]
    s.people_responses=[]
    assert run_scenario(conn,s)["decisions"][0]["decision"]=="hold"
    request=conn.execute("SELECT request_id FROM info_requests").fetchone()[0]
    questions.answer(conn,request,reply["person"],reply["data"])
    d=run_scenario(conn,s)["decisions"][0]
    assert d["decision"]=="classify_and_book"
    assert "bewirtung_docs_missing" in d["flags"] and d["approvers"]==["tax_manager"]
    entry_request=conn.execute("SELECT request_id,subject_ref FROM approval_requests WHERE status='pending'").fetchone()
    assert gates.active_holds(conn)
    approvals.decide(conn,person_actor(conn,"tax_manager"),entry_request[0],"approve","Evidence is now complete")
    assert not gates.active_holds(conn)
    gates.post_approved_entry(conn,person_actor(conn,"controller"),entry_request[1])


def test_year_end_prepares_summaries_and_notice_drafts_without_filing(public_case):
    conn,s=public_case("C01")
    s.tasks=[{"type":"run_year_end"}]
    result=run_scenario(conn,s)
    summary=result["year_end"]
    assert summary["year"]==2026
    hospitality=next(row for row in summary["non_deductible_summary"] if row["account"]=="6410" and row["status"]=="draft")
    assert hospitality["amount"]=="225.00"
    assert summary["recipient_notices"] and all(row["status"]=="draft" for row in summary["recipient_notices"])
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE origin='vouch' AND status='posted'").fetchone()[0]==0


def test_real_unicode_generic_meal_occasion_requires_specific_purpose():
    from vouch.calc.eventtax import hospitality_docs
    rules=Rules(rows_for()[0]["rules"])
    result=hospitality_docs("Gesch\u00e4ftsessen",True,"host",D("119"),True,True,rules,"2026-10-01")
    assert result.values["missing"]==["specific occasion"]
