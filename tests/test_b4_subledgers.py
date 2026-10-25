"""B4: accounts payable per vendor and input VAT per event roll up to the general ledger."""
from decimal import Decimal as D

from vouch.db import subledger


def test_input_vat_per_event_or_item(db):
    assert subledger.vat_by_event(db) == {"EVT-FAS26": D("1140.00"), "GIFT-0318:1": D("5.70")}


def test_payables_per_vendor(db):
    assert subledger.ap_by_vendor(db) == {"V-CATER": D("-7140.00"), "(no vendor)": D("-35.70")}


def test_subledgers_roll_up_to_the_ledger(db):
    assert subledger.rollup(db) == []
