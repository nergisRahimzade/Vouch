"""B4: accounts payable per vendor and prepaid per event roll up to the general ledger."""
from decimal import Decimal as D

from vouch.db import subledger


def test_prepaid_per_event_holds_every_paid_deposit(db):
    # Deposits net of VAT: Bayview 60,000; Golden Fork 6,375 (both SF26); Coastline 15,000; Thames 11,400 +
    # Kettle 3,410 (London); Hudson 56,000.
    assert subledger.prepaid_by_event(db) == {"EVT-SF26": D("66375.00"), "EVT-OFF26": D("15000.00"),
                                              "EVT-LDN26": D("14810.00"), "EVT-NYC26": D("56000.00")}


def test_deposits_are_paid_so_nothing_is_owed(db):
    assert set(subledger.ap_by_vendor(db).values()) == {D("0.00")}


def test_subledgers_roll_up_to_the_ledger(db):
    assert subledger.rollup(db) == []
