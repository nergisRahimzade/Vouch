"""T3: the matching engine, rule by rule (spec §5.2), with boundaries. Every expected amount is worked
out by hand in the comment beside it, never taken from the engine."""
import json
from decimal import Decimal as D

import pytest

from vouch.calc.matching import (MalformedInvoice, MatchInput, cancellation_pct, match, round_up_to,
                                 venue_overtime_hours)
from vouch.world.scenario import rows_for

ROWS, _ = rows_for()
OPS = {"P-OPS"}


def evidence(*ids, overrides=None, extra=()):
    out = []
    for e in ROWS["evidence"]:
        if e["id"] in ids:
            row = {**e, "value": dict(e["value"])}
            row["value"].update((overrides or {}).get(e["id"], {}))
            out.append(row)
    return out + [dict(x) for x in extra]


def inp(contract, po, lines, ev=(), invoice=None, deposits=None):
    con = next(c for c in ROWS["contracts"] if c["id"] == contract)
    clauses = {c["clause_id"]: {**c, "params": dict(c["params"])} for c in ROWS["contract_clauses"]
               if c["contract"] == contract}
    purchase = next((p for p in ROWS["purchase_orders"] if p["id"] == po), None)
    if purchase:
        purchase = {**purchase, "lines": [l for l in ROWS["po_lines"] if l["po"] == po]}
    if deposits is None:
        deposits = []
        for i in ROWS["invoices"]:
            if i.get("contract") == contract and any(p["invoice"] == i["id"] for p in ROWS["payments"]):
                lines_ = [l for l in ROWS["invoice_lines"] if l["invoice"] == i["id"]]
                net = sum(D(str(l["amount"])) for l in lines_ if l["kind"] == "deposit")
                vat = sum(D(str(l["amount"])) for l in lines_ if l["kind"] == "vat")
                deposits.append({"clause_ref": lines_[0]["clause_ref"], "invoice": i["id"], "net": net, "vat": vat,
                                 "gross": net + vat})
    event = next(e for e in ROWS["events"] if e["id"] == con["event"])
    vendor = next(v for v in ROWS["vendors"] if v["id"] == con["vendor"])
    number = 1
    for line in lines:
        line.setdefault("id", f"L{number}")
        line.setdefault("line_no", number)
        number += 1
    inv = {"id": "INV-T", "vendor": con["vendor"], "currency": vendor["currency"], "lines": lines, **(invoice or {})}
    return MatchInput(invoice=inv, contract=con, clauses=clauses, po=purchase, evidence=list(ev), deposits=deposits,
                      policies={p["id"]: str(p.get("value")) for p in ROWS["policies"]}, event=event, vendor=vendor,
                      ops_people=OPS, owner=event["owner"])


def flags(result, kind=None):
    return sorted({i.flag for i in result.issues if kind is None or i.kind == kind})


# -- catering per head ------------------------------------------------------------------------
def catering(qty, unit="85.00", svc=None, tax=None, credit="-6375.00", ev=("EV-SF26-BADGE-0611",), **kw):
    food = D(str(qty)) * D(unit)
    svc = svc if svc is not None else str(food * D("0.20"))
    tax = tax if tax is not None else str((food + D(svc)) * D("0.08625"))
    from vouch.money import cents
    lines = [{"kind": "item", "description": "Summit lunch", "qty": qty, "unit_price": unit, "amount": str(food),
              "clause_ref": "CAT-PRICE", "service_date": "2026-06-11"},
             {"kind": "service_charge", "amount": str(cents(D(svc))), "clause_ref": "CAT-SVC"},
             {"kind": "tax", "amount": str(cents(D(tax))), "clause_ref": "CAT-TAX"}]
    if credit:
        lines.append({"kind": "deposit_credit", "amount": credit, "clause_ref": "CAT-DEPOSIT"})
    return inp("C-GFORK-SF26", "PO-SF26-CAT", lines, evidence(*ev, **kw))


def test_headcount_above_the_guarantee_and_the_badges_is_disputed():
    # Billed 340 x 85 = 28,900; svc 5,780; tax 0.08625 x 34,680 = 2,991.15; -6,375 => 31,296.15.
    # Badges 280 < guarantee 300 => 300 billable: 25,500; svc 5,100; tax 0.08625 x 30,600 = 2,639.25; -6,375
    # => 26,864.25 approved; 31,296.15 - 26,864.25 = 4,431.90 disputed.
    result = match(catering(340))
    assert result.billed == D("31296.15")
    assert (result.approved, result.held, result.disputed) == (D("26864.25"), D("0.00"), D("4431.90"))
    assert flags(result) == ["headcount_overbilled"]
    food = result.lines[0]
    assert food.source_of_truth == "badge scans" and food.evidence_ids == ["EV-SF26-BADGE-0611"]
    assert {"CAT-PRICE", "CAT-GUARANTEE"} <= set(food.clause_ids)


def test_count_exactly_equal_to_the_guarantee_is_clean():
    # 300 billed, badges 300: 300 x 85 = 25,500 + 5,100 + 2,639.25 - 6,375 = 26,864.25, nothing to dispute.
    result = match(catering(300, overrides={"EV-SF26-BADGE-0611": {"lunch_attendees": 300}}))
    assert (result.approved, result.disputed, flags(result)) == (D("26864.25"), D("0.00"), [])


def test_served_above_the_invoice_pays_the_invoice_and_flags_underbilling():
    # 310 billed, 320 badged: pay the 310 billed (never more than the invoice asks); info flag only.
    result = match(catering(310, overrides={"EV-SF26-BADGE-0611": {"lunch_attendees": 320}}))
    assert result.approved == result.billed and result.disputed == D("0.00")
    assert flags(result, "info") == ["possible_underbilling"] and flags(result, "dispute") == []


def test_staff_roster_counts_only_when_the_contract_says_so():
    # Badges 280 + staff 25 = 305 when staff count: 305 x 85 = 25,925; svc 5,185; tax 0.08625 x 31,110
    # = 2,683.2375 -> 2,683.24; -6,375 => 27,418.24.
    data = catering(340, ev=("EV-SF26-BADGE-0611", "EV-SF26-STAFF-0611"))
    assert match(data).approved == D("26864.25")                 # the contract does not count staff
    data.clauses["CAT-STAFF"]["params"]["counts_toward_headcount"] = True
    assert match(data).approved == D("27418.24")


def test_signed_meal_count_beats_badges_and_rate_above_contract_is_disputed():
    # Kettle: 231 x 68 = 15,708; svc 12.5% 1,963.50; VAT 20% of 17,671.50 = 3,534.30; -4,092 => 17,113.80.
    # Signed count 231 (badges 228): 231 x 62 = 14,322; svc 1,790.25; VAT 20% of 16,112.25 = 3,222.45;
    # -4,092 => 15,242.70 approved; 1,871.10 disputed.
    lines = [{"kind": "item", "description": "Forum lunch", "qty": 231, "unit_price": "68.00", "amount": "15708.00",
              "clause_ref": "KTL-PRICE", "service_date": "2026-09-24"},
             {"kind": "service_charge", "amount": "1963.50", "clause_ref": "KTL-SVC"},
             {"kind": "vat", "amount": "3534.30", "clause_ref": "KTL-VAT"},
             {"kind": "deposit_credit", "amount": "-4092.00", "clause_ref": "KTL-DEPOSIT"}]
    result = match(inp("C-KETTLE-LDN26", "PO-LDN26-CAT", lines, evidence("EV-LDN26-BADGE", "EV-LDN26-MEAL")))
    assert result.billed == D("17113.80")
    assert (result.approved, result.disputed) == (D("15242.70"), D("1871.10"))
    assert flags(result) == ["rate_above_contract"]
    assert result.lines[0].source_of_truth == "meal count signed by event ops"


def test_no_evidence_pays_the_guarantee_and_holds_the_rest():
    # 340 billed, no badges or signed count: 300 approved now (26,864.25), the other 40 meals with their
    # service charge and tax held (31,296.15 - 26,864.25 = 4,431.90), event ops asked.
    result = match(catering(340, ev=()))
    assert (result.approved, result.held, result.disputed) == (D("26864.25"), D("4431.90"), D("0.00"))
    assert flags(result) == ["missing_evidence"]
    assert {i.request["about"] for i in result.issues if i.request} == {"signed_meal_count"}


# -- fixed amounts -----------------------------------------------------------------------------
def test_fixed_price_inclusive_catering():
    clean = match(inp("C-GFORK-SF26", "PO-SF26-DIN", [{"kind": "item", "description": "Speaker dinner",
                                                        "amount": "14800.00", "clause_ref": "GF-DIN"}]))
    assert (clean.approved, flags(clean)) == (D("14800.00"), [])
    over = match(inp("C-GFORK-SF26", "PO-SF26-DIN", [{"kind": "item", "amount": "14800.00", "clause_ref": "GF-DIN"},
                                                      {"kind": "tax", "amount": "1276.50"}]))
    assert (over.approved, over.disputed, flags(over)) == (D("14800.00"), D("1276.50"), ["rate_above_contract"])


def test_uk_venue_hire_with_vat_and_a_gross_deposit_credit():
    # 38,000 + VAT 7,600 - 13,680 (deposit credited gross) = 31,920.
    lines = [{"kind": "item", "description": "Venue hire", "amount": "38000.00", "clause_ref": "THM-HIRE"},
             {"kind": "vat", "amount": "7600.00", "clause_ref": "THM-HIRE"},
             {"kind": "deposit_credit", "amount": "-13680.00", "clause_ref": "THM-DEPOSIT"}]
    result = match(inp("C-THAMES-LDN26", "PO-LDN26-VEN", lines))
    assert (result.approved, result.disputed, flags(result)) == (D("31920.00"), D("0.00"), [])


def test_deposit_credited_net_charges_its_vat_twice():
    # Credit -11,400 instead of -13,680: the deposit's 2,280 VAT is charged again => 2,280 disputed.
    lines = [{"kind": "item", "amount": "38000.00", "clause_ref": "THM-HIRE"},
             {"kind": "vat", "amount": "7600.00", "clause_ref": "THM-HIRE"},
             {"kind": "deposit_credit", "amount": "-11400.00", "clause_ref": "THM-DEPOSIT"}]
    result = match(inp("C-THAMES-LDN26", "PO-LDN26-VEN", lines))
    assert (result.approved, result.disputed) == (D("31920.00"), D("2280.00"))
    assert flags(result) == ["vat_on_deposit_double_charged"]


def test_deposit_not_applied():
    # Bayview final bills 120,000 with no credit for the 60,000 deposit: 60,000 approved, 60,000 disputed.
    result = match(inp("C-BAYV-SF26", "PO-SF26-VEN", [{"kind": "item", "description": "Venue hire",
                                                        "amount": "120000.00", "clause_ref": "VEN-RENT"}]))
    assert (result.approved, result.disputed, flags(result)) == (D("60000.00"), D("60000.00"), ["deposit_not_applied"])


def test_deposit_invoice_is_a_deposit():
    result = match(inp("C-HUDSON-NYC26", "PO-NYC26-VEN", [{"kind": "deposit", "amount": "56000.00",
                                                            "clause_ref": "HUD-DEPOSIT"}], deposits=[]))
    assert (result.invoice_kind, result.approved, flags(result)) == ("deposit", D("56000.00"), [])


# -- venue overtime ------------------------------------------------------------------------------
@pytest.mark.parametrize("ended, hours", [("2026-06-12T23:00", "0"), ("2026-06-12T23:01", "0.5"),
                                          ("2026-06-12T23:30", "0.5"), ("2026-06-12T23:31", "1.0"),
                                          ("2026-06-13T00:00", "1.0"), ("2026-06-13T00:47", "2.0"),
                                          ("2026-06-12T22:15", "0")])
def test_venue_overtime_rounds_up_to_half_hours(ended, hours):
    assert venue_overtime_hours(ended, "23:00", D("0.5")) == D(hours)


def test_round_up_to_increment_at_zero_and_one_minute():
    assert round_up_to(D(0), D("0.5")) == 0 and round_up_to(D(1) / 60, D("0.5")) == D("0.5")


def test_venue_overtime_from_the_load_out_log():
    # Ended 00:47 => 2.0 h x 1,500 = 3,000, as billed.
    line = {"kind": "fee", "description": "Load-out overtime", "qty": "2.0", "unit_price": "1500.00",
            "amount": "3000.00", "clause_ref": "VEN-OT"}
    result = match(inp("C-BAYV-SF26", "PO-SF26-VEN", [line], evidence("EV-SF26-LOADOUT")))
    assert (result.approved, flags(result)) == (D("3000.00"), [])
    over = match(inp("C-BAYV-SF26", "PO-SF26-VEN", [{**line, "qty": "3.0", "amount": "4500.00"}],
                     evidence("EV-SF26-LOADOUT")))
    assert (over.approved, over.disputed, flags(over)) == (D("3000.00"), D("1500.00"), ["overtime_overbilled"])


# -- AV --------------------------------------------------------------------------------------------
def test_day_rates_only_for_days_on_the_po():
    on_po = match(inp("C-LUMEN-SF26", "PO-SF26-AV", [{"kind": "item", "description": "Rehearsal day", "qty": 1,
                                                       "unit_price": "4800.00", "amount": "4800.00",
                                                       "clause_ref": "AV-DAYRATE.rehearsal"}]))
    assert (on_po.approved, flags(on_po)) == (D("4800.00"), [])
    off_po = match(inp("C-LUMEN-SF26", "PO-SF26-AV", [{"kind": "item", "description": "Extra build day", "qty": 1,
                                                        "unit_price": "4800.00", "amount": "4800.00",
                                                        "clause_ref": "AV-DAYRATE", "service_date": "2026-06-09"}]))
    assert (off_po.approved, off_po.held, flags(off_po)) == (D("0.00"), D("4800.00"), ["unapproved_change_order"])


def test_crew_overtime_from_the_crew_log():
    # Billed 6 crew x 4.0 h x 225 = 5,400. Log: 07:00-21:30 = 14.5 h, 2.5 h over 12; 6 x 2.5 x 225 = 3,375.
    line = {"kind": "fee", "description": "Crew overtime", "qty": 24, "unit_price": "225.00", "amount": "5400.00",
            "clause_ref": "AV-OT", "service_date": "2026-06-11", "detail": {"crew": 6, "hours": "4.0"}}
    result = match(inp("C-LUMEN-SF26", "PO-SF26-AV", [line], evidence("EV-SF26-CREW-0611", "EV-SF26-CREW-0612")))
    assert (result.approved, result.disputed, flags(result)) == (D("3375.00"), D("2025.00"), ["overtime_overbilled"])
    # Without a service date both days' logs count: 06-12 is 11 h, no overtime, so the answer is the same.
    del line["service_date"]
    assert match(inp("C-LUMEN-SF26", "PO-SF26-AV", [line],
                     evidence("EV-SF26-CREW-0611", "EV-SF26-CREW-0612"))).approved == D("3375.00")


def test_change_order_needs_the_event_owners_signature():
    lines = [{"kind": "item", "description": "AV production package", "amount": "48000.00", "clause_ref": "AV-PKG"},
             {"kind": "item", "description": "LED wall upgrade", "amount": "7500.00", "clause_ref": "AV-CO"}]
    unsigned = match(inp("C-LUMEN-SF26", "PO-SF26-AV", [dict(l) for l in lines]))
    assert (unsigned.approved, unsigned.held, flags(unsigned)) == (D("48000.00"), D("7500.00"),
                                                                   ["unapproved_change_order"])
    assert [i.request for i in unsigned.issues] == [{"to_role": "event_owner", "about": "signed_change_order"}]
    co = {"id": "EV-CO-1", "event": "EVT-SF26", "type": "change_order", "date": "2026-06-05", "signed_by": "P-OWN-SF",
          "value": {"vendor": "V-LUMEN", "description": "LED wall upgrade", "amount": "7500.00"}}
    signed = match(inp("C-LUMEN-SF26", "PO-SF26-AV", [dict(l) for l in lines], [co]))
    assert (signed.approved, flags(signed)) == (D("55500.00"), [])
    by_someone_else = match(inp("C-LUMEN-SF26", "PO-SF26-AV", [dict(l) for l in lines], [{**co, "signed_by": "P-OPS"}]))
    assert by_someone_else.held == D("7500.00")


# -- merchandise -------------------------------------------------------------------------------------
def hoodies(delivered, signed_by="P-OPS"):
    line = {"kind": "item", "description": "Hoodies", "item": "hoodie", "qty": 350, "unit_price": "32.00",
            "amount": "11200.00", "clause_ref": "SWG-UNIT"}
    ev = evidence("EV-SF26-DELIV-HOOD", overrides={"EV-SF26-DELIV-HOOD": {"qty": delivered}})
    ev[0]["signed_by"] = signed_by
    return match(inp("C-SWAG-SF26", "PO-SF26-SWAG", [line], ev))


def test_goods_paid_on_quantity_delivered():
    # 350 billed, 310 delivered: 310 x 32 = 9,920 approved; 40 x 32 = 1,280 held until delivered.
    partial = hoodies(310)
    assert (partial.approved, partial.held, partial.disputed) == (D("9920.00"), D("1280.00"), D("0.00"))
    assert flags(partial) == ["partial_delivery"]
    full = hoodies(350)
    assert (full.approved, flags(full)) == (D("11200.00"), [])


def test_an_unsigned_delivery_receipt_is_not_proof():
    unsigned = hoodies(350, signed_by=None)
    assert (unsigned.approved, unsigned.held) == (D("0.00"), D("11200.00"))


def test_speaker_gifts():
    line = {"kind": "item", "description": "Speaker gifts", "qty": 60, "unit_price": "40.00", "amount": "2400.00",
            "clause_ref": "SWG-UNIT"}
    result = match(inp("C-SWAG-SF26", "PO-SF26-GIFT", [line], evidence("EV-SF26-DELIV-GIFT")))
    assert (result.approved, flags(result)) == (D("2400.00"), [])


# -- lodging -------------------------------------------------------------------------------------------
def coastline(pickup):
    lines = [{"kind": "item", "description": "Room block", "qty": 120, "unit_price": "310.00", "amount": "37200.00",
              "clause_ref": "LDG-BLOCK"},
             {"kind": "item", "description": "Meeting space", "amount": "9000.00", "clause_ref": "MTG-SPACE"},
             {"kind": "deposit_credit", "amount": "-15000.00", "clause_ref": "LDG-DEPOSIT"}]
    return match(inp("C-COAST-OFF26", "PO-OFF26-LDG", lines,
                     evidence("EV-OFF26-PICKUP", overrides={"EV-OFF26-PICKUP": {"room_nights": pickup}})))


def test_attrition_minimum_applies_when_pickup_is_below_it():
    # Pickup 82 < minimum 96: 96 x 310 = 29,760 + 9,000 - 15,000 = 23,760; 31,200 - 23,760 = 7,440 disputed.
    result = coastline(82)
    assert (result.billed, result.approved, result.disputed) == (D("31200.00"), D("23760.00"), D("7440.00"))
    assert flags(result) == ["attrition_misapplied"]


def test_zero_room_nights_still_pays_the_minimum():
    assert coastline(0).approved == D("23760.00")


def test_pickup_above_the_minimum_is_what_is_owed():
    # 100 x 310 = 31,000 + 9,000 - 15,000 = 25,000.
    assert coastline(100).approved == D("25000.00")


# -- cancellation ---------------------------------------------------------------------------------------
TIERS = [{"min_days": 90, "pct": "0.25"}, {"min_days": 60, "pct": "0.50"}, {"min_days": 0, "pct": "1.00"}]


@pytest.mark.parametrize("days, pct", [(120, "0.25"), (90, "0.25"), (89, "0.50"), (60, "0.50"), (59, "1.00"),
                                       (0, "1.00")])
def test_cancellation_tier_min_days_is_inclusive(days, pct):
    assert cancellation_pct(TIERS, days) == D(pct)


def test_cancellation_exactly_sixty_days_before_the_event():
    # Hudson event 2026-12-09; notice 2026-10-10 is exactly 60 days before => 50% of 140,000 = 70,000.
    clause = {"clause_id": "CXL-TIERS", "contract": "C-HUDSON-NYC26", "type": "cancellation",
              "params": {"tiers": TIERS, "base": "HUD-HIRE"}, "text": ""}
    notice = {"id": "EV-CXL", "event": "EVT-NYC26", "type": "cancellation_notice", "date": "2026-10-10",
              "value": {"notice_date": "2026-10-10"}}
    data = inp("C-HUDSON-NYC26", "PO-NYC26-VEN", [{"kind": "fee", "description": "Cancellation fee",
                                                    "amount": "140000.00", "clause_ref": "CXL-TIERS"}], [notice],
               deposits=[])
    data.clauses["CXL-TIERS"] = clause
    result = match(data)
    assert (result.approved, result.disputed, flags(result)) == (D("70000.00"), D("70000.00"),
                                                                 ["cancellation_tier_wrong"])


# -- malformed --------------------------------------------------------------------------------------------
def test_lines_that_do_not_add_up_to_the_total_are_malformed():
    data = inp("C-GFORK-SF26", "PO-SF26-COF", [{"kind": "item", "amount": "3900.00", "clause_ref": "GF-COF"}],
               invoice={"total": "4000.00"})
    with pytest.raises(MalformedInvoice):
        match(data)
    with pytest.raises(MalformedInvoice):
        match(inp("C-GFORK-SF26", "PO-SF26-COF", [{"kind": "item", "amount": None, "clause_ref": "GF-COF"}]))
