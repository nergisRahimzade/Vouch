"""B3: journal entries are dated, have a reason, carry entity and department on expense lines,
link to their source rows, and must balance or they are refused."""
import json
from collections import defaultdict
from decimal import Decimal

import pytest

from vouch.db.ledger import Entry, EntryRefused, Line, SourceRef, UnbalancedEntry, post_entry
from vouch.world import config as C


def test_the_whole_booked_ledger_is_posted(world, db):
    expected = {l["entry_id"] for m in world.months() for l in world.csv(f"months/{m}/ledger_booked.csv")}
    posted = {r[0] for r in db.execute("SELECT entry_id FROM journal_entries WHERE origin = 'booked_ledger'")}
    assert posted == expected
    assert db.execute("SELECT COUNT(*) FROM refused_entries").fetchone()[0] == 0


def test_every_entry_balances_and_every_line_has_evidence(db):
    totals = defaultdict(Decimal)
    for r in db.execute("SELECT entry_id, debit, credit FROM journal_lines"):
        totals[r["entry_id"]] += Decimal(r["debit"]) - Decimal(r["credit"])
    assert totals and not [e for e, v in totals.items() if v]
    missing = db.execute("""SELECT COUNT(*) FROM journal_lines l WHERE NOT EXISTS (
        SELECT 1 FROM line_sources s WHERE s.entry_id = l.entry_id AND s.line_no = l.line_no AND s.role = 'evidence')""")
    assert missing.fetchone()[0] == 0
    no_department = db.execute("""SELECT COUNT(*) FROM journal_lines l JOIN accounts a ON a.code = l.account
        WHERE a.type = 'expense' AND (l.department IS NULL OR l.department = '')""")
    assert no_department.fetchone()[0] == 0


def test_wage_lines_link_to_the_register_rows_that_add_up_to_them(db):
    lines = db.execute("SELECT entry_id, line_no, debit FROM journal_lines WHERE account = '6000'").fetchall()
    assert lines
    for line in lines:
        rows = db.execute("""SELECT r.data FROM line_sources s JOIN source_rows r USING (doc_id, row_no)
            WHERE s.entry_id = ? AND s.line_no = ? AND s.role = 'evidence'""", (line["entry_id"], line["line_no"]))
        assert sum(Decimal(json.loads(r[0])["gross_pay"]) for r in rows) == Decimal(line["debit"])


def test_opening_balances_are_booked_and_first_remittances_cite_them(world, db):
    opening = world.csv("standing/opening_balances.csv")
    for entity in ("US", "UK", "DE"):
        booked = {r["account"]: Decimal(r["debit"]) - Decimal(r["credit"]) for r in db.execute(
            "SELECT account, debit, credit FROM journal_lines WHERE entry_id = ?", (f"JE-{C.FIRST_FILE_MONTH}-{entity}-OPENING",))}
        expected = {r["account"]: Decimal(r["debit"]) - Decimal(r["credit"]) for r in opening if r["entity"] == entity}
        assert booked == {a: v for a, v in expected.items() if v}
        assert sum(booked.values()) == 0
    cited = db.execute("""SELECT COUNT(*) FROM line_sources s JOIN journal_entries e USING (entry_id)
        JOIN documents d ON d.doc_id = s.doc_id
        WHERE e.entry_id LIKE ? AND d.path = 'standing/opening_balances.csv' AND s.role = 'evidence'""",
                       (f"JE-{C.FIRST_FILE_MONTH}-%-REMIT-%",))
    assert cited.fetchone()[0] > 0


def _evidence(conn):
    doc = conn.execute("SELECT doc_id FROM documents WHERE kind = 'payroll_register' AND month = ?",
                       (C.OPEN_MONTH,)).fetchone()[0]
    return [SourceRef(doc, 1)]


def _entry(conn, **overrides):
    lines = overrides.pop("lines", None) or [
        Line("6000", debit=Decimal("100.00"), department="Engineering", sources=_evidence(conn)),
        Line("2100", credit=Decimal("100.00"), sources=_evidence(conn)),
    ]
    fields = dict(entry_id="TEST-ENTRY", entity="US", period=C.OPEN_MONTH, entry_date=f"{C.OPEN_MONTH}-15",
                  reason="test accrual", lines=lines, created_by="Vouch")
    return Entry(**{**fields, **overrides})


def test_a_balanced_entry_is_accepted(scratch_db):
    post_entry(scratch_db, _entry(scratch_db))
    assert scratch_db.execute("SELECT COUNT(*) FROM journal_lines WHERE entry_id = 'TEST-ENTRY'").fetchone()[0] == 2


def test_an_unbalanced_entry_is_rejected_and_nothing_is_written(scratch_db):
    lines = [Line("6000", debit=Decimal("100.00"), department="Engineering", sources=_evidence(scratch_db)),
             Line("2100", credit=Decimal("99.99"), sources=_evidence(scratch_db))]
    with pytest.raises(UnbalancedEntry, match="differ by 0.01"):
        post_entry(scratch_db, _entry(scratch_db, lines=lines))
    assert scratch_db.execute("SELECT COUNT(*) FROM journal_entries WHERE entry_id = 'TEST-ENTRY'").fetchone()[0] == 0


@pytest.mark.parametrize("change, reason", [
    (lambda c: dict(lines=[Line("7777", debit=Decimal("1"), sources=_evidence(c)),
                           Line("2100", credit=Decimal("1"), sources=_evidence(c))]), "not in the chart"),
    (lambda c: dict(lines=[Line("6000", debit=Decimal("1"), sources=_evidence(c)),
                           Line("2100", credit=Decimal("1"), sources=_evidence(c))]), "needs a department"),
    (lambda c: dict(lines=[Line("6000", debit=Decimal("1"), department="Engineering"),
                           Line("2100", credit=Decimal("1"), sources=_evidence(c))]), "no source rows"),
    (lambda c: dict(reason="  "), "no reason"),
    (lambda c: dict(entry_date="2026-01-15"), "outside period"),
    (lambda c: dict(entity="FR"), "unknown entity"),
    (lambda c: dict(lines=[Line("6000", debit=Decimal("1.005"), department="Engineering", sources=_evidence(c)),
                           Line("2100", credit=Decimal("1.005"), sources=_evidence(c))]), "two decimals"),
])
def test_entries_missing_what_the_ledger_needs_are_refused(scratch_db, change, reason):
    with pytest.raises(EntryRefused, match=reason):
        post_entry(scratch_db, _entry(scratch_db, **change(scratch_db)))


def test_an_entry_id_cannot_be_reused(scratch_db):
    post_entry(scratch_db, _entry(scratch_db))
    with pytest.raises(EntryRefused, match="already exists"):
        post_entry(scratch_db, _entry(scratch_db))
