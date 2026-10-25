"""B3: journal entries are dated, typed, have a reason, carry cost center and event on expense lines,
link to the rows they rest on, and must balance or they are refused."""
from collections import defaultdict
from decimal import Decimal

import pytest

from vouch.db.ledger import Entry, EntryRefused, Line, SourceRef, UnbalancedEntry, post_entry

SRC = [SourceRef("rules", "R-BV-ALLOW")]


def test_the_history_is_posted_and_balances(db):
    assert db.execute("SELECT COUNT(*) FROM refused_entries").fetchone()[0] == 0
    totals = defaultdict(Decimal)
    for r in db.execute("SELECT entry_id, debit, credit FROM journal_lines"):
        totals[r["entry_id"]] += Decimal(r["debit"]) - Decimal(r["credit"])
    assert totals and not [e for e, v in totals.items() if v]


def _entry(**overrides):
    lines = overrides.pop("lines", None) or [
        Line("6440", debit=Decimal("100.00"), cost_center="CC-PROD", event="EVT-XMAS26", sources=SRC),
        Line("1600", credit=Decimal("100.00"), sources=SRC)]
    fields = dict(entry_id="TEST-ENTRY", entity="ENT-DE", period="2026-12", entry_date="2026-12-15", type="staff_event",
                  reason="test staff event", lines=lines, created_by="Vouch")
    return Entry(**{**fields, **overrides})


def test_a_balanced_entry_is_accepted(db):
    post_entry(db, _entry())
    assert db.execute("SELECT COUNT(*) FROM journal_lines WHERE entry_id = 'TEST-ENTRY'").fetchone()[0] == 2


def test_an_unbalanced_entry_is_rejected_and_nothing_is_written(db):
    lines = [Line("6440", debit=Decimal("100.00"), cost_center="CC-PROD", event="EVT-XMAS26", sources=SRC),
             Line("1600", credit=Decimal("99.99"), sources=SRC)]
    with pytest.raises(UnbalancedEntry, match="differ by 0.01"):
        post_entry(db, _entry(lines=lines))
    assert db.execute("SELECT COUNT(*) FROM journal_entries WHERE entry_id = 'TEST-ENTRY'").fetchone()[0] == 0


@pytest.mark.parametrize("change, reason", [
    (dict(lines=[Line("7777", debit=Decimal("1"), sources=SRC), Line("1600", credit=Decimal("1"), sources=SRC)]),
     "not in the chart"),
    (dict(lines=[Line("6440", debit=Decimal("1"), sources=SRC), Line("1600", credit=Decimal("1"), sources=SRC)]),
     "needs a cost center"),
    (dict(lines=[Line("6440", debit=Decimal("1"), cost_center="CC-PROD", event="EVT-XMAS26"),
                 Line("1600", credit=Decimal("1"), sources=SRC)]), "no source rows"),
    (dict(lines=[Line("6440", debit=Decimal("1"), cost_center="CC-PROD", event="EVT-XMAS26",
                      sources=[SourceRef("rules", "NOPE")]),
                 Line("1600", credit=Decimal("1"), sources=SRC)]), "does not exist"),
    (dict(reason="  "), "no reason"),
    (dict(entry_date="2026-01-15"), "outside period"),
    (dict(entity="ENT-FR"), "unknown entity"),
    (dict(type="bonus"), "unknown entry type"),
    (dict(lines=[Line("6440", debit=Decimal("1.005"), cost_center="CC-PROD", event="EVT-XMAS26", sources=SRC),
                 Line("1600", credit=Decimal("1.005"), sources=SRC)]), "two decimals"),
])
def test_entries_missing_what_the_ledger_needs_are_refused(db, change, reason):
    with pytest.raises(EntryRefused, match=reason):
        post_entry(db, _entry(**change))


def test_an_entry_id_cannot_be_reused(db):
    post_entry(db, _entry())
    with pytest.raises(EntryRefused, match="already exists"):
        post_entry(db, _entry())
