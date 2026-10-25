"""Contract intake: every clause is stored with a structured type (spec §5.1, stage 1). A clause that
names its type keeps it; one that does not (a scenario may add clauses written only as params) is
typed from the shape of its terms, then from its ID. A clause whose type cannot be told is stored
untyped, and anything billed against it is held, never guessed."""
from __future__ import annotations

TYPES = {"venue_hire", "meeting_space", "fixed_fee", "deposit", "venue_overtime", "per_head_price", "guarantee",
         "staff_count", "service_charge", "sales_tax", "vat", "fixed_price", "early_pay_terms", "av_package",
         "day_rate", "crew_overtime", "change_order", "unit_items", "delivery_basis", "lodging_block", "attrition",
         "cancellation"}

# (type, keys that must all be present in params), checked in order.
BY_PARAMS = [
    ("change_order", {"requires_signed_change_order_by"}),
    ("cancellation", {"tiers"}),
    ("early_pay_terms", {"discount_pct", "discount_days"}),
    ("per_head_price", {"price_per_head"}),
    ("guarantee", {"guaranteed_count"}),
    ("staff_count", {"counts_toward_headcount"}),
    ("service_charge", {"service_charge_pct"}),
    ("sales_tax", {"rate_ref"}),
    ("sales_tax", {"tax_pct"}),
    ("crew_overtime", {"rate_per_crew_hour"}),
    ("venue_overtime", {"rate_per_hour", "after"}),
    ("unit_items", {"items"}),
    ("delivery_basis", {"bill_on"}),
    ("lodging_block", {"room_nights", "rate"}),
    ("attrition", {"min_room_nights"}),
    ("attrition", {"min_pct"}),
    ("fixed_price", {"fixed"}),
    ("av_package", {"show_days"}),
    ("day_rate", {"days", "rate"}),
    ("deposit", {"credited_on_final"}),
    ("deposit", {"applied_to"}),
    ("deposit", {"gross"}),
    ("vat", {"vat_pct", "base"}),
    ("venue_hire", {"net", "vat_pct"}),
]
# Words in a clause ID, used only when the params say nothing.
BY_ID = [("DEPOSIT", "deposit"), ("CXL", "cancellation"), ("CANCEL", "cancellation"), ("MTG", "meeting_space"),
         ("MEET", "meeting_space"), ("SPACE", "meeting_space"), ("HIRE", "venue_hire"), ("RENT", "venue_hire")]


def clause_type(clause: dict) -> str | None:
    given = clause.get("type")
    if given:
        return str(given)
    params = clause.get("params") or {}
    keys = set(params)
    for kind, needed in BY_PARAMS:
        if needed <= keys:
            return kind
    clause_id = str(clause.get("clause_id", "")).upper()
    for word, kind in BY_ID:
        if word in clause_id:
            return kind
    if "amount" in keys:
        return "fixed_fee"
    return None
