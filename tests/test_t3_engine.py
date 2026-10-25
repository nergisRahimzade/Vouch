"""T3: the event tax engine, rule by rule (spec §6.1), with the boundaries the spec names and POL-ROUND.
Every expected amount is worked out by hand in the comment beside it, never taken from the engine."""
from decimal import Decimal as D

import pytest

from vouch.calc import eventtax as et
from vouch.world.scenario import rows_for

RULES = et.Rules(rows_for()[0]["rules"])
ON = "2026-07-03"


def attendees(employees, hosts=(), each=1):
    rows = [{"person_id": e, "person_kind": "employee"} for e in employees]
    for host in hosts:
        rows += [{"person_id": f"C-{host}-{n}", "person_kind": "companion", "host_employee": host}
                 for n in range(1, each + 1)]
    return rows


SUMMER = attendees([f"E{n:03d}" for n in range(1, 301)], [f"E{n:03d}" for n in range(201, 261)])


def test_summer_party_allocation_with_companions():
    # 8,330 + 24,990 + 4,165 + 1,190 = 38,675 gross over 360 = 107.430555...; a single employee 107.43;
    # a host with one companion round(214.8611...) = 214.86.
    result = et.allocate_staff_event(D("38675.00"), SUMMER, RULES, ON)
    assert result.values["participants"] == 360
    assert result.values["per_participant_rounded"] == D("107.43")
    assert result.values["per_participant"] > D("107.4305") and result.values["per_participant"] < D("107.4306")
    assert result.values["attributed"]["E001"] == D("107.43") and result.values["attributed"]["E201"] == D("214.86")
    assert "R-BV-COST v1" in result.rules and "POL-ROUND" in result.policies


def test_late_bill_allocation():
    # 38,675 + 1,428 = 40,103 over 360 = 111.397222... -> 111.40; host round(222.7944...) = 222.79.
    result = et.allocate_staff_event(D("40103.00"), SUMMER, RULES, ON)
    assert result.values["per_participant_rounded"] == D("111.40")
    assert result.values["attributed"]["E260"] == D("222.79")


@pytest.mark.parametrize("companions, attributed", [(0, "50.00"), (1, "100.00"), (2, "150.00")])
def test_companion_counts(companions, attributed):
    # 4 people at EUR 200 total... built so the per-participant cost is exactly 50.00.
    people = attendees(["E001"], ["E001"], companions) + attendees([f"E{n:03d}" for n in range(2, 5 - companions)])
    total = D(50) * len(people)
    assert et.allocate_staff_event(total, people, RULES, ON).values["attributed"]["E001"] == D(attributed)


def test_attendance_is_never_estimated():
    with pytest.raises(ValueError):
        et.allocate_staff_event(D("100.00"), [], RULES, ON)


@pytest.mark.parametrize("per_participant, recoverable", [("107.43", True), ("110.00", True), ("110.004", True),
                                                          ("110.005", False), ("110.01", False), ("111.40", False)])
def test_vat_freigrenze_at_110(per_participant, recoverable):
    assert et.staff_event_vat_test(D(per_participant), RULES, ON).values["recoverable"] is recoverable


def test_allowance_spares_only_the_excess():
    # Host at 214.86 with the allowance: 104.86 taxable; without it: all 214.86; a single at 107.43: 0.
    assert et.taxable_after_allowance(D("214.86"), True, RULES, ON) == D("104.86")
    assert et.taxable_after_allowance(D("214.86"), False, RULES, ON) == D("214.86")
    assert et.taxable_after_allowance(D("107.43"), True, RULES, ON) == D("0.00")
    assert et.taxable_after_allowance(D("110.00"), True, RULES, ON) == D("0.00")
    assert et.taxable_after_allowance(D("110.01"), True, RULES, ON) == D("0.01")
    assert et.exempt_amount(D("214.86"), RULES, ON) == D("110.00") and et.exempt_amount(D("35.70"), RULES, ON) == D("35.70")


def test_staff_event_needs_more_than_half_staff_and_openness():
    # Customer day: 40 employees of 160 = 25% -> not a staff event. Exactly half -> not more than half.
    assert et.qualifies_as_staff_event(True, 40, 0, 120, RULES, ON).values["qualifies"] is False
    assert et.qualifies_as_staff_event(True, 50, 0, 50, RULES, ON).values["qualifies"] is False
    assert et.qualifies_as_staff_event(True, 300, 60, 0, RULES, ON).values["qualifies"] is True
    assert et.qualifies_as_staff_event(False, 25, 0, 0, RULES, ON).values["qualifies"] is False


@pytest.mark.parametrize("net, deductible, nondeductible", [("750.00", "525.00", "225.00"), ("500.00", "350.00", "150.00"),
                                                            ("11000.00", "7700.00", "3300.00"), ("0.01", "0.01", "0.00"),
                                                            ("33.33", "23.33", "10.00")])
def test_hospitality_seventy_thirty(net, deductible, nondeductible):
    # round(33.33 x 0.70 = 23.331) = 23.33; round(0.007) = 0.01.
    result = et.hospitality_split(D(net), RULES, ON)
    assert (result.values["deductible"], result.values["nondeductible"]) == (D(deductible), D(nondeductible))


def test_hospitality_documentation():
    generic = et.hospitality_docs("Geschäftsessen", False, "P-SALES-HEAD", D("892.50"), True, True, RULES, ON)
    assert generic.values["missing"] == ["participants", "specific occasion"]
    full = et.hospitality_docs("Projektabschluss Linie 4 mit Bergmann Automotive", True, "P-SALES-HEAD", D("892.50"),
                               True, True, RULES, ON)
    assert full.values["complete"] is True
    no_host = et.hospitality_docs("Projektabschluss", True, None, D("251.00"), True, True, RULES, ON)
    assert no_host.values["missing"] == ["host (required on an invoice over EUR 250.00)"]


def gift(id_, day, net, vat, recipient="BP-003"):
    return et.Gift(id_, day, D(net), D(vat), D(net) + D(vat), recipient)


def test_gift_ledger_over_fifty_makes_every_gift_of_the_year_nondeductible():
    # 30.00 + 25.00 net = 55.00 > 50: both non-deductible at gross (35.70, 29.75), VAT blocked (5.70, 4.75).
    result = et.gift_ledger([gift("G1", "2026-03-18", "30.00", "5.70"), gift("G2", "2026-12-07", "25.00", "4.75")],
                            2026, RULES)
    assert (result.values["total"], result.values["deductible"]) == (D("55.00"), False)
    assert result.values["items"]["G2"]["nondeductible_amount"] == D("29.75")
    assert result.values["items"]["G1"]["vat_blocked"] == D("5.70")


def test_gift_ledger_exactly_fifty_is_deductible():
    result = et.gift_ledger([gift("G1", "2026-03-18", "30.00", "5.70"), gift("G2", "2026-12-07", "20.00", "3.80")],
                            2026, RULES)
    assert (result.values["total"], result.values["deductible"]) == (D("50.00"), True)
    assert result.values["items"]["G2"]["vat_recoverable"] == D("3.80")


def test_gift_limit_before_2024_is_35_and_gross_counts_without_vat():
    old = et.gift_ledger([gift("G1", "2023-05-01", "36.00", "6.84")], 2023, RULES)
    assert (old.values["limit"], old.values["deductible"]) == (D("35.00"), False)
    # No VAT on the invoice: the gross counts. 48.00 gross alone is under 50; with another 3.00 it is over.
    no_vat = et.gift_ledger([gift("G1", "2026-02-01", "48.00", "0.00"), gift("G2", "2026-03-01", "3.00", "0.00")],
                            2026, RULES)
    assert (no_vat.values["total"], no_vat.values["deductible"]) == (D("51.00"), False)
    assert et.gift_ledger([gift("G1", "2025-02-01", "40.00", "7.60")], 2026, RULES).values["total"] == D("0.00")


def test_rule_versions_follow_the_date():
    assert RULES.cite("R-GIFT-VER", "2023-12-31") == "R-GIFT-VER v1"
    assert RULES.cite("R-GIFT-VER", "2024-01-01") == "R-GIFT-VER v2"
    with pytest.raises(et.RuleMissing):
        RULES.value("R-NOPE", ON)


@pytest.mark.parametrize("unit_net, promo", [("2.50", True), ("10.00", True), ("10.01", False), ("14.00", False)])
def test_promo_item_at_ten_euro(unit_net, promo):
    assert et.is_promo_item(D(unit_net), RULES, ON) is promo


@pytest.mark.parametrize("base, tax, soli, church, total", [
    ("6291.60", "1572.90", "86.51", "110.10", "1769.51"),   # 6,291.60 x .25; 1,572.90 x .055 = 86.5095; x .07 = 110.103
    ("5355.00", "1338.75", "73.63", "93.71", "1506.09"),    # 1,338.75 x .055 = 73.63125; x .07 = 93.7125
    ("811.80", "202.95", "11.16", "14.21", "228.32"),       # 202.95 x .055 = 11.16225; x .07 = 14.2065
])
def test_flat_tax_40(base, tax, soli, church, total):
    v = et.flat_tax_40(D(base), RULES, ON).values
    assert (v["tax"], v["surcharge"], v["church_tax"], v["total"]) == (D(tax), D(soli), D(church), D(total))


@pytest.mark.parametrize("base, total", [
    ("8330.00", "2811.38"),   # 2,499.00; 137.445 -> 137.45; 174.93 => 2,811.38
    ("35.70", "12.05"),       # 10.71; 0.58905 -> 0.59; 0.7497 -> 0.75 => 12.05
    ("29.75", "10.05"),       # 8.925 -> 8.93; 0.49115 -> 0.49; 0.6251 -> 0.63 => 10.05
    ("47.60", "16.07"),       # 14.28; 0.7854 -> 0.79; 0.9996 -> 1.00 => 16.07
    ("3808.00", "1285.20"),   # 1,142.40; 62.832 -> 62.83; 79.968 -> 79.97 => 1,285.20
])
def test_flat_tax_37b(base, total):
    assert et.flat_tax_37b(D(base), RULES, ON).values["total"] == D(total)


def test_37b_cap_at_ten_thousand():
    assert et.flat_tax_37b_available(D("10000.00"), RULES, ON).values["available"] is True
    assert et.flat_tax_37b_available(D("10000.01"), RULES, ON).values["available"] is False


def test_partner_share_by_headcount():
    # Program gross 4,760 x 60 partners / 75 attendees = 3,808.00.
    assert et.share_by_headcount(D("4760.00"), 60, 75) == D("3808.00")


@pytest.mark.parametrize("gross, persons, within", [("571.20", 12, True), ("720.00", 12, True), ("720.12", 12, False),
                                                    ("61.00", 1, False)])
def test_working_meal_at_sixty_euro(gross, persons, within):
    # 571.20 / 12 = 47.60; 720.00 / 12 = 60.00; 720.12 / 12 = 60.01.
    assert et.working_meal_test(D(gross), persons, RULES, ON).values["within_limit"] is within


def test_vat_arithmetic_is_validated():
    good = {"id": "L1", "net": "7000.00", "vat": "1330.00", "gross": "8330.00", "vat_rate": "0.19"}
    assert et.vat_arithmetic([good]) == []
    assert et.vat_arithmetic([{**good, "gross": "8331.00"}]) == ["L1: net 7000.00 + VAT 1330.00 is not gross 8331.00"]
    assert et.vat_arithmetic([{**good, "vat": "1400.00", "gross": "8400.00"}]) == ["L1: VAT 1400.00 is not 0.19 of net 7000.00"]
    assert et.vat_arithmetic([{"id": "L2", "net": "1.00"}]) == ["L2: net, VAT or gross missing"]
