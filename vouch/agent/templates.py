"""Entries the agent drafts (spec §6.3). Code computes every amount from the matching result, the
deposits and the schedule; the model only decides whether an entry is needed. Each template returns a
draft Entry that balances (the ledger refuses one that does not) and names the rows it rests on.

  deposit invoice   Dr 1310 (+ Dr 1400 VAT for ENT-UK) / Cr 2000
  final invoice     Dr expense / Cr 2000 for the approved amount net of the deposit sitting in 1310
  prepaid release   Dr expense / Cr 1310 on the event's last day
  accrual           Dr expense / Cr 2100 at month end, reversed on the 1st of the next month
  reclass           in the current open period (closed months are never edited)
  payment discount  Dr 2000 full / Cr 1000 net / Cr 6190 discount

Anything dated in a closed month is booked on the as_of date in the current period and names the month
it belongs to (corrects_period)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vouch import dates
from vouch.calc import schedule
from vouch.calc.matching import MatchResult
from vouch.calc.rounding import ACCOUNT_ORDER
from vouch.db.ledger import Entry, Line, SourceRef
from vouch.money import ZERO, cents

VAT_RECEIVABLE_ENTITIES = {"ENT-UK"}       # UK VAT on ENT-UK purchases is reclaimable (1400)


class CannotDraft(ValueError):
    """The entry cannot be built from what is known; said plainly, never guessed."""


@dataclass(frozen=True)
class Books:
    """What the templates need to know about the books: the date, and which months are closed."""
    as_of: date
    locked: frozenset

    def place(self, day: date) -> tuple[str, str, str | None]:
        """(period, entry date, corrects_period) for something that happened on `day`."""
        period = dates.month_of(day)
        if period in self.locked:
            return dates.month_of(self.as_of), self.as_of.isoformat(), period
        return period, day.isoformat(), None


def _lines(amounts: dict[str, Decimal], tags: dict, sources: dict[str, list[SourceRef]], memo: str) -> list[Line]:
    """One line per account in chart order: positive amounts debit, negative credit."""
    out = []
    for account in sorted(amounts, key=ACCOUNT_ORDER.index):
        value = cents(amounts[account])
        if value == 0:
            continue
        out.append(Line(account, debit=value if value > 0 else ZERO, credit=-value if value < 0 else ZERO,
                        cost_center=tags["cost_center"], event=tags["event"], memo=memo,
                        sources=sources.get(account, [])))
    return out


def tags_of(event: dict) -> dict:
    return {"cost_center": event.get("cost_center"), "event": event.get("id")}


def invoice_entry(result: MatchResult, invoice: dict, event: dict, books: Books, clauses: dict) -> Entry:
    """AP entry for the approved part of an invoice. Held and disputed parts are not booked."""
    if result.approved <= 0:
        raise CannotDraft("nothing is approved, so there is nothing to book")
    entity = invoice["entity"]
    reclaim_vat = entity in VAT_RECEIVABLE_ENTITIES
    amounts: dict[str, Decimal] = defaultdict(lambda: ZERO)
    sources: dict[str, list[SourceRef]] = defaultdict(list)

    def book(account: str | None, value: Decimal, line, clause_ids):
        if not account:
            raise CannotDraft(f"no expense account is known for {line.description or line.line_id}")
        amounts[account] += value
        if line.line_id:
            sources[account].append(SourceRef("invoice_lines", line.line_id))
        sources[account] += [SourceRef("contract_clauses", c) for c in clause_ids if c in clauses]

    for line in result.lines:
        if line.strict == 0:
            continue
        if result.invoice_kind == "deposit":
            book("1400" if line.is_vat and reclaim_vat else "1310", line.strict, line, line.clause_ids)
        elif line.kind == "deposit_credit":
            params = clauses.get(line.clause_ids[0], {}).get("params", {}) if line.clause_ids else {}
            gross, vat = Decimal(str(params.get("gross", "0") or "0")), Decimal(str(params.get("vat", "0") or "0"))
            vat_part = cents(line.strict * vat / gross) if reclaim_vat and gross and vat else ZERO
            book(line.account, line.strict - vat_part, line, line.clause_ids)
            if vat_part:
                book("1400", vat_part, line, line.clause_ids)
        elif line.is_vat and reclaim_vat:
            book("1400", line.strict, line, line.clause_ids)
        else:
            book(line.account, line.strict, line, line.clause_ids)
    amounts["2000"] = -result.approved
    sources["2000"] = [SourceRef("invoices", invoice["id"])]
    period, day, corrects = books.place(date.fromisoformat(str(invoice["invoice_date"])[:10]))
    kind = "ap_deposit" if result.invoice_kind == "deposit" else "ap_invoice"
    reason = (f"Deposit invoice {invoice['id']} to prepaid" if kind == "ap_deposit" else
              f"Invoice {invoice['id']}: approved amount net of the deposit already in prepaid")
    if corrects:
        reason += f" (belongs to closed period {corrects})"
    return Entry(entry_id=f"VCH-AP-{invoice['id']}", entity=entity, period=period, entry_date=day, type=kind,
                 reason=reason, lines=_lines(amounts, tags_of(event), sources, invoice["id"]), created_by="Vouch",
                 source_ref=invoice["id"], corrects_period=corrects)


@dataclass(frozen=True)
class Item:
    """One amount to book on one expense account, with the rows it rests on."""
    account: str
    amount: Decimal
    sources: tuple = ()


def release_entry(event: dict, entity: str, items: list[Item], books: Books) -> Entry:
    """Prepaid release on the event's last day: Dr expense / Cr 1310."""
    total = sum((i.amount for i in items), ZERO)
    if total <= 0:
        raise CannotDraft(f"no prepaid balance for {event['id']}")
    amounts = defaultdict(lambda: ZERO)
    sources = defaultdict(list)
    for item in items:
        amounts[item.account] += item.amount
        sources[item.account] += list(item.sources)
    amounts["1310"] = -total
    sources["1310"] = [s for i in items for s in i.sources]
    period, day, corrects = books.place(schedule.release_date(event))
    return Entry(entry_id=f"VCH-REL-{event['id']}", entity=entity, period=period, entry_date=day,
                 type="prepaid_release", reason=f"Release of prepaid deposits for {event['id']} on its last day",
                 lines=_lines(amounts, tags_of(event), sources, event["id"]), created_by="Vouch",
                 source_ref=event["id"], corrects_period=corrects)


def accrual_entries(period: str, event: dict, entity: str, items: list[Item], books: Books) -> tuple[Entry, Entry]:
    """Accrual at month end (Dr expense / Cr 2100) and its automatic reversal on the 1st of the next month."""
    total = sum((i.amount for i in items), ZERO)
    if total <= 0:
        raise CannotDraft(f"nothing to accrue for {event['id']} in {period}")
    if period in books.locked:
        raise CannotDraft(f"{period} is closed; an accrual cannot be booked into it")
    amounts = defaultdict(lambda: ZERO)
    sources = defaultdict(list)
    for item in items:
        amounts[item.account] += item.amount
        sources[item.account] += list(item.sources)
    amounts["2100"] = -total
    sources["2100"] = [s for i in items for s in i.sources]
    tags = tags_of(event)
    accrual_id = f"VCH-ACR-{event['id']}-{period}"
    accrual = Entry(entry_id=accrual_id, entity=entity, period=period,
                    entry_date=schedule.accrual_date(period).isoformat(), type="accrual",
                    reason=f"Accrual for {event['id']} services delivered in {period} and not yet invoiced",
                    lines=_lines(amounts, tags, sources, event["id"]), created_by="Vouch", source_ref=event["id"])
    mirror = {account: -value for account, value in amounts.items()}
    reversal_day = schedule.reversal_date(period)
    reversal = Entry(entry_id=f"VCH-REV-{event['id']}-{period}", entity=entity, period=dates.month_of(reversal_day),
                     entry_date=reversal_day.isoformat(), type="accrual_reversal",
                     reason=f"Automatic reversal of accrual {accrual_id}",
                     lines=_lines(mirror, tags, sources, event["id"]), created_by="Vouch", source_ref=event["id"],
                     reverses=accrual_id)
    return accrual, reversal


def reclass_entry(original_id: str, original_period: str, event: dict, entity: str, from_account: str,
                  amount: Decimal, books: Books, to_account: str = "1310") -> Entry:
    """Move an amount booked to the wrong account, in the current open period: Dr to_account / Cr from_account."""
    if amount <= 0:
        raise CannotDraft("nothing to reclassify")
    sources = {to_account: [SourceRef("journal_entries", original_id)],
               from_account: [SourceRef("journal_entries", original_id)]}
    period = dates.month_of(books.as_of)
    return Entry(entry_id=f"VCH-RCL-{original_id}", entity=entity, period=period, entry_date=books.as_of.isoformat(),
                 type="reclass", reason=f"Reclass of {original_id}: {from_account} to {to_account}",
                 lines=_lines({to_account: amount, from_account: -amount}, tags_of(event), sources, original_id),
                 created_by="Vouch", source_ref=original_id,
                 corrects_period=original_period if original_period != period else None)


def payment_entry(proposal: schedule.Proposal, invoice: dict, event: dict, books: Books) -> Entry:
    """The payment a person will release: Dr 2000 full / Cr 1000 net (/ Cr 6190 discount)."""
    amounts = {"2000": proposal.full, "1000": -proposal.amount}
    if proposal.discount:
        amounts["6190"] = -proposal.discount
    sources = {a: [SourceRef("invoices", invoice["id"])] for a in amounts}
    period, day, corrects = books.place(proposal.pay_by)
    return Entry(entry_id=f"VCH-PAY-{invoice['id']}", entity=invoice["entity"], period=period, entry_date=day,
                 type="discount" if proposal.discount else "payment",
                 reason=f"Payment of {invoice['id']} to the account on file ending {proposal.account_last4}"
                        + (" with the early-pay discount" if proposal.discount else ""),
                 lines=_lines(amounts, tags_of(event), sources, invoice["id"]), created_by="Vouch",
                 source_ref=invoice["id"], corrects_period=corrects)
