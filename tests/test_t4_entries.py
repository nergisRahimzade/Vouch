"""T4: every entry type balances and books what spec §6.3 says, dated per the schedule and the period
lock. Amounts are worked out by hand in the comments."""
from datetime import date
from decimal import Decimal as D

import pytest

from test_t3_matching import catering, coastline, inp
from vouch.agent.templates import (Books, CannotDraft, Item, accrual_entries, invoice_entry, payment_entry,
                                   reclass_entry, release_entry)
from vouch.calc import schedule
from vouch.calc.matching import match
from vouch.db.ledger import SourceRef, balance, post_entry

OPEN_JUNE = Books(date(2026, 6, 20), frozenset(f"2026-{m:02d}" for m in range(1, 6)))
LATE = Books(date(2026, 10, 6), frozenset(f"2026-{m:02d}" for m in range(1, 10)))
SF26 = {"id": "EVT-SF26", "cost_center": "CC-DEVREL", "end_date": "2026-06-12"}
OFF26 = {"id": "EVT-OFF26", "cost_center": "CC-RES", "end_date": "2026-08-20"}
LDN26 = {"id": "EVT-LDN26", "cost_center": "CC-EVT", "end_date": "2026-09-24"}
NYC26 = {"id": "EVT-NYC26", "cost_center": "CC-DEVREL", "end_date": "2026-12-09"}


def lines_of(entry):
    return sorted((l.account, l.debit, l.credit) for l in entry.lines)


def test_final_invoice_books_the_approved_amount_net_of_the_deposit():
    # P01: food 25,500 + svc 5,100 + tax 2,639.25 (US tax is expense) - deposit 6,375 = 26,864.25 to 6110.
    data = catering(340)
    entry = invoice_entry(match(data), {"id": "INV-T", "entity": "ENT-US", "invoice_date": "2026-06-15"}, SF26,
                          OPEN_JUNE, data.clauses)
    assert lines_of(entry) == [("2000", D("0.00"), D("26864.25")), ("6110", D("26864.25"), D("0.00"))]
    assert (entry.type, entry.entry_date, entry.corrects_period, balance(entry)) == ("ap_invoice", "2026-06-15",
                                                                                     None, 0)
    assert all(l.event == "EVT-SF26" and l.cost_center == "CC-DEVREL" for l in entry.lines)


def test_uk_invoice_books_vat_to_1400_net_of_the_deposit_vat():
    # KC-3301 approved 15,242.70: food+svc 16,112.25 less deposit net 3,410 = 12,702.25 to 6110;
    # VAT 3,222.45 less deposit VAT 682 = 2,540.45 to 1400.
    lines = [{"kind": "item", "qty": 231, "unit_price": "68.00", "amount": "15708.00", "clause_ref": "KTL-PRICE",
              "service_date": "2026-09-24"},
             {"kind": "service_charge", "amount": "1963.50", "clause_ref": "KTL-SVC"},
             {"kind": "vat", "amount": "3534.30", "clause_ref": "KTL-VAT"},
             {"kind": "deposit_credit", "amount": "-4092.00", "clause_ref": "KTL-DEPOSIT"}]
    from test_t3_matching import evidence
    data = inp("C-KETTLE-LDN26", "PO-LDN26-CAT", lines, evidence("EV-LDN26-MEAL"))
    entry = invoice_entry(match(data), {"id": "KC-T", "entity": "ENT-UK", "invoice_date": "2026-09-28"}, LDN26,
                          Books(date(2026, 10, 2), frozenset()), data.clauses)
    assert lines_of(entry) == [("1400", D("2540.45"), D("0.00")), ("2000", D("0.00"), D("15242.70")),
                               ("6110", D("12702.25"), D("0.00"))]


def test_deposit_invoice_goes_to_prepaid_not_expense():
    data = inp("C-HUDSON-NYC26", "PO-NYC26-VEN", [{"kind": "deposit", "amount": "56000.00",
                                                    "clause_ref": "HUD-DEPOSIT"}], deposits=[])
    entry = invoice_entry(match(data), {"id": "INV-HP-T", "entity": "ENT-US", "invoice_date": "2026-09-10"}, NYC26,
                          Books(date(2026, 9, 12), frozenset()), data.clauses)
    assert entry.type == "ap_deposit"
    assert lines_of(entry) == [("1310", D("56000.00"), D("0.00")), ("2000", D("0.00"), D("56000.00"))]


def test_a_late_invoice_for_a_closed_month_is_booked_on_as_of_in_the_current_period():
    from test_t3_matching import evidence
    data = inp("C-BAYV-SF26", "PO-SF26-VEN", [{"kind": "fee", "qty": "2.0", "unit_price": "1500.00",
                                                "amount": "3000.00", "clause_ref": "VEN-OT"}],
               evidence("EV-SF26-LOADOUT"))
    entry = invoice_entry(match(data), {"id": "BV-T", "entity": "ENT-US", "invoice_date": "2026-06-30"}, SF26, LATE,
                          data.clauses)
    assert (entry.period, entry.entry_date, entry.corrects_period) == ("2026-10", "2026-10-06", "2026-06")
    assert lines_of(entry) == [("2000", D("0.00"), D("3000.00")), ("6100", D("3000.00"), D("0.00"))]


def test_nothing_approved_means_no_entry():
    data = inp("C-LUMEN-SF26", "PO-SF26-AV", [{"kind": "item", "description": "LED wall", "amount": "7500.00",
                                                "clause_ref": "AV-CO"}])
    with pytest.raises(CannotDraft):
        invoice_entry(match(data), {"id": "X", "entity": "ENT-US", "invoice_date": "2026-06-20"}, SF26, OPEN_JUNE,
                      data.clauses)


def test_release_accrual_and_reversal_for_the_offsite(scenario_db):
    # P10: release Dr 6140 15,000 / Cr 1310 on 2026-08-20; accrual 96 x 310 - 15,000 = 14,760 lodging +
    # 9,000 meeting space = 23,760 to 2100 on 2026-08-31; reversal mirrors it on 2026-09-01.
    books = Books(date(2026, 8, 31), frozenset(f"2026-{m:02d}" for m in range(1, 7)))
    src = (SourceRef("journal_entries", "JE-CR-8700"),)
    release = release_entry(OFF26, "ENT-US", [Item("6140", D("15000.00"), src)], books)
    assert (release.entry_date, lines_of(release)) == ("2026-08-20", [("1310", D("0.00"), D("15000.00")),
                                                                      ("6140", D("15000.00"), D("0.00"))])
    clause = (SourceRef("contract_clauses", "LDG-BLOCK"),)
    accrual, reversal = accrual_entries("2026-08", OFF26, "ENT-US", [Item("6140", D("14760.00"), clause),
                                                                     Item("6100", D("9000.00"), clause)], books)
    assert (accrual.entry_date, reversal.entry_date, reversal.reverses) == ("2026-08-31", "2026-09-01", accrual.entry_id)
    assert lines_of(accrual) == [("2100", D("0.00"), D("23760.00")), ("6100", D("9000.00"), D("0.00")),
                                 ("6140", D("14760.00"), D("0.00"))]
    assert lines_of(reversal) == [("2100", D("23760.00"), D("0.00")), ("6100", D("0.00"), D("9000.00")),
                                  ("6140", D("0.00"), D("14760.00"))]
    conn = scenario_db({"id": "T", "as_of": "2026-08-31"})
    for entry in (release, accrual, reversal):
        post_entry(conn, entry)                    # balanced, and every source exists
    with pytest.raises(CannotDraft):
        accrual_entries("2026-06", OFF26, "ENT-US", [Item("6140", D("1.00"), clause)], books)


def test_reclass_of_a_deposit_wrongly_expensed():
    # P09: Dr 1310 56,000 / Cr 6100 56,000 on 2026-10-31, in the open period, naming September.
    books = Books(date(2026, 10, 31), frozenset(f"2026-{m:02d}" for m in range(1, 10)))
    entry = reclass_entry("JE-LEGACY", "2026-09", NYC26, "ENT-US", "6100", D("56000.00"), books)
    assert (entry.period, entry.entry_date, entry.corrects_period) == ("2026-10", "2026-10-31", "2026-09")
    assert lines_of(entry) == [("1310", D("56000.00"), D("0.00")), ("6100", D("0.00"), D("56000.00"))]


@pytest.mark.parametrize("text, expected", [("net_30", (30, D(0), 0)), ("net 14", (14, D(0), 0)),
                                            ("2/10 net 30", (30, D("0.02"), 10)), (None, (30, D(0), 0))])
def test_payment_terms(text, expected):
    terms = schedule.parse_terms(text)
    assert (terms.net_days, terms.discount_pct, terms.discount_days) == expected


def test_early_pay_discount_only_inside_its_window():
    # 12,400 on 2/10 net 30 dated 2026-11-02: approved 11-05 -> pay 12,152 by 11-12 with 248 off;
    # approved 11-13 -> pay 12,400 by 12-02.
    terms = schedule.parse_terms("2/10 net 30", {"discount_pct": "0.02", "discount_days": 10, "net_days": 30})
    early = schedule.payment_proposal(D("12400.00"), "2026-11-02", terms, date(2026, 11, 5), "8820")
    assert early.to_dict() == {"amount": "12152.00", "pay_by": "2026-11-12", "discount": "248.00",
                               "account_last4": "8820"}
    late = schedule.payment_proposal(D("12400.00"), "2026-11-02", terms, date(2026, 11, 13), "8820")
    assert late.to_dict() == {"amount": "12400.00", "pay_by": "2026-12-02", "discount": "0.00", "account_last4": "8820"}
    entry = payment_entry(early, {"id": "GF-T", "entity": "ENT-US"}, NYC26, Books(date(2026, 11, 5), frozenset()))
    assert lines_of(entry) == [("1000", D("0.00"), D("12152.00")), ("2000", D("12400.00"), D("0.00")),
                               ("6190", D("0.00"), D("248.00"))]
    assert entry.type == "discount"


def test_schedule_dates():
    assert schedule.release_date(OFF26) == date(2026, 8, 20)
    assert schedule.accrual_date("2026-08") == date(2026, 8, 31)
    assert schedule.reversal_date("2026-12") == date(2027, 1, 1)
    assert schedule.pay_by("2026-06-15", schedule.parse_terms("net_15")) == date(2026, 6, 30)


def test_coastline_final_invoice_after_attrition():
    # Approved 23,760: lodging 96 x 310 = 29,760 - deposit 15,000 = 14,760 to 6140; meeting space 9,000 to 6100.
    result = coastline(82)
    data_clauses = inp("C-COAST-OFF26", "PO-OFF26-LDG", []).clauses
    entry = invoice_entry(result, {"id": "CR-T", "entity": "ENT-US", "invoice_date": "2026-09-08"}, OFF26,
                          Books(date(2026, 9, 15), frozenset()), data_clauses)
    assert lines_of(entry) == [("2000", D("0.00"), D("23760.00")), ("6100", D("9000.00"), D("0.00")),
                               ("6140", D("14760.00"), D("0.00"))]
