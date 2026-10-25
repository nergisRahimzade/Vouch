"""T4: per-person ledgers, the allowance optimizer and corrections against what was booked (spec §5.6).
The retro cases P01, P04, P11 and P03 at unit level; amounts worked out by hand in the comments."""
from decimal import Decimal as D

from vouch.calc import eventtax as et
from vouch.calc.ledgers import (BookedGift, EventAttribution, gift_corrections, optimize_allowances, payroll_changes,
                                staff_ledger, vat_correction)
from vouch.world.scenario import rows_for

RULES = et.Rules(rows_for()[0]["rules"])
E = lambda a, b: [f"E{n:03d}" for n in range(a, b + 1)]


def summer(per_single="107.43", per_host="214.86"):
    attributed = {e: D(per_single) for e in E(1, 300)}
    attributed.update({e: D(per_host) for e in E(201, 260)})
    return EventAttribution("EVT-SOM26", "2026-07-03", True, attributed)


FASCHING = EventAttribution("EVT-FAS26", "2026-02-12", True, {e: D("35.70") for e in E(1, 200)})
XMAS = EventAttribution("EVT-XMAS26", "2026-12-11", True, {e: D("95.20") for e in E(1, 150) + E(301, 450)})
SUMMER_BOOKED = {(e, "EVT-SOM26"): D("104.86") for e in E(201, 260)}


def test_first_processing_of_the_summer_party_is_new_taxable_pay():
    # P01: 60 hosts x (214.86 - 110) = 60 x 104.86 = 6,291.60; singles at 107.43 stay under the allowance.
    ledger = staff_ledger([FASCHING, summer()], RULES)
    changes = payroll_changes(ledger, {}, {"EVT-FAS26"}, {"EVT-SOM26"}, RULES)
    assert changes.new_total == D("6291.60") and len(changes.new) == 60
    assert changes.deltas == {} and changes.corrections == [] and changes.reassigned == []


def test_a_third_event_moves_the_allowance_and_corrects_the_first():
    # P04: E001-E150 have Fasching 35.70, Sommer 107.43, Xmas 95.20. Allowance to the two that spare the most
    # (107.43 and 95.20); Fasching becomes taxable: 150 x 35.70 = 5,355.00 as a correction of EVT-FAS26.
    ledger = staff_ledger([FASCHING, summer(), XMAS], RULES)
    assert ledger.assignment["E001"] == ["EVT-SOM26", "EVT-XMAS26"]
    assert ledger.assignment["E151"] == ["EVT-SOM26", "EVT-FAS26"]          # two events: both keep it
    changes = payroll_changes(ledger, SUMMER_BOOKED, {"EVT-FAS26", "EVT-SOM26"}, {"EVT-XMAS26"}, RULES)
    assert changes.new == {}                                                 # Xmas itself creates no taxable pay
    assert [(c.type, c.amount, c.ref) for c in changes.corrections] == [("payroll_delta", D("5355.00"), "EVT-FAS26")]
    assert changes.reassigned == E(1, 150)
    flat = et.flat_tax_40(changes.new_total + changes.delta_total, RULES, "2026-12-18")
    assert flat.values["total"] == D("1506.09")


def test_ties_go_to_the_earlier_event():
    # Three events each attributing 120 (all spare the full 110): the allowance goes to the two earliest.
    a = EventAttribution("EVT-A", "2026-03-01", True, {"E001": D("120.00")})
    b = EventAttribution("EVT-B", "2026-05-01", True, {"E001": D("120.00")})
    c = EventAttribution("EVT-C", "2026-04-01", True, {"E001": D("120.00")})
    assert optimize_allowances([a, b, c], RULES) == {"E001": ["EVT-A", "EVT-C"]}
    ledger = staff_ledger([a, b, c], RULES)
    assert [ledger.taxable("E001", x) for x in ("EVT-A", "EVT-B", "EVT-C")] == [D("10.00"), D("120.00"), D("10.00")]


def test_a_non_qualifying_event_never_gets_the_allowance():
    # Managers-only dinner: 4,165 / 25 = 166.60 each, all of it taxable.
    dinner = EventAttribution("EVT-FK26", "2026-09-18", False, {e: D("166.60") for e in E(10, 34)})
    ledger = staff_ledger([dinner], RULES)
    assert ledger.taxable("E010", "EVT-FK26") == D("166.60") and "E010" not in ledger.assignment
    changes = payroll_changes(ledger, {}, set(), {"EVT-FK26"}, RULES)
    assert changes.new_total == D("4165.00")


def test_a_late_bill_changes_every_attendee_and_reverses_vat():
    # P11: 111.40 per single (+1.40 each for 240 singles = 336.00) and 222.79 per host (112.79 vs 104.86 booked:
    # +7.93 each for 60 = 475.80): payroll delta 811.80; flat tax 228.32; VAT 6,175 reclaimed in July reversed.
    ledger = staff_ledger([FASCHING, summer("111.40", "222.79")], RULES)
    changes = payroll_changes(ledger, SUMMER_BOOKED, {"EVT-FAS26", "EVT-SOM26"}, {"EVT-SOM26"}, RULES)
    assert changes.new == {}
    assert [(c.type, c.amount, c.ref) for c in changes.corrections] == [("payroll_delta", D("811.80"), "EVT-SOM26")]
    assert changes.reassigned == []
    assert et.flat_tax_40(changes.delta_total, RULES, "2026-09-14").values["total"] == D("228.32")
    reversal = vat_correction("EVT-SOM26", False, D("6175.00"), RULES, "2026-09-14")
    assert [(c.type, c.amount) for c in reversal] == [("vat_reversal", D("6175.00"))]
    assert vat_correction("EVT-SOM26", True, D("6175.00"), RULES, "2026-09-14") == []


def test_a_december_gift_reverses_the_march_deduction():
    # P03: BP-003 had 30.00 net in March (deductible, VAT 5.70 reclaimed, §37b 12.05 to 6450). December adds 25.00
    # net: 55.00 > 50. Corrections: reclass 30.00 6420->6430, VAT reversal 5.70, reclass 12.05 6450->6460.
    gifts = [et.Gift("GIFT-0318", "2026-03-18", D("30.00"), D("5.70"), D("35.70"), "BP-003"),
             et.Gift("INV-GIFT-1207:1", "2026-12-07", D("25.00"), D("4.75"), D("29.75"), "BP-003")]
    current = et.gift_ledger(gifts, 2026, RULES)
    booked = [BookedGift("GIFT-0318", "BP-003", True, D("30.00"), D("5.70"), D("12.05"), "6450")]
    fixes = gift_corrections(current, booked, RULES, "2026-12-15")
    assert [(c.type, c.amount, c.from_account, c.to_account) for c in fixes] == [
        ("reclass", D("30.00"), "6420", "6430"), ("vat_reversal", D("5.70"), "1400", "6430"),
        ("reclass", D("12.05"), "6450", "6460")]
    december = current.values["items"]["INV-GIFT-1207:1"]
    assert (december["nondeductible_amount"], december["vat_blocked"]) == (D("29.75"), D("4.75"))
    assert et.flat_tax_37b(D("29.75"), RULES, "2026-12-15").values["total"] == D("10.05")


def test_a_gift_within_the_limit_needs_no_correction():
    gifts = [et.Gift("G1", "2026-03-18", D("30.00"), D("5.70"), D("35.70"), "BP-003"),
             et.Gift("G2", "2026-12-07", D("20.00"), D("3.80"), D("23.80"), "BP-003")]
    booked = [BookedGift("G1", "BP-003", True, D("30.00"), D("5.70"), D("12.05"), "6450")]
    assert gift_corrections(et.gift_ledger(gifts, 2026, RULES), booked, RULES, "2026-12-15") == []
