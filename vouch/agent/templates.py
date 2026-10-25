"""Event-tax journal drafts. All money is supplied by deterministic calculations."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vouch.db.ledger import Entry, Line, SourceRef, balance
from vouch.money import ZERO, cents


class CannotDraft(ValueError):
    pass


@dataclass(frozen=True)
class Books:
    as_of: date
    locked: frozenset

    def place(self, day):
        period = str(day)[:7]
        if period in self.locked:
            return self.as_of.isoformat()[:7], self.as_of.isoformat(), period
        return period, str(day)[:10], None


def make_entry(entry_id, kind, event, entity, amounts, books, day, sources, *, item=None,
               person=None, reason="Event tax treatment", corrects=None):
    period, entry_date, original = books.place(day)
    if period in books.locked:
        raise CannotDraft("the current period is locked")
    if not sources:
        raise CannotDraft("every entry needs source rows")
    if not event.get("cost_center"):
        raise CannotDraft("an expense needs a cost center")
    lines = []
    for account, value in sorted(amounts.items()):
        value = cents(Decimal(str(value)))
        if value:
            lines.append(Line(str(account), debit=max(value, ZERO), credit=max(-value, ZERO),
                              event=event.get("id"), cost_center=event["cost_center"], item=item,
                              person=person, sources=list(sources), memo=reason))
    entry = Entry(entry_id, entity, period, entry_date, kind, reason, lines, "Vouch",
                  source_ref=item or event.get("id"), corrects_period=original, corrects=corrects)
    if len(lines) < 2 or balance(entry):
        raise CannotDraft("entry must have at least two lines and balance exactly")
    return entry


def invoice_entry(invoice, event, treatment, books, *, entry_id=None):
    """Book only a documented, computed treatment. Held or escalated parts have no entry."""
    if treatment.get("decision") in {"hold", "escalate", "reject_duplicate", "no_action"}:
        raise CannotDraft("the treatment is not ready for booking")
    classification = treatment["classification"]
    amounts = invoice_amounts(invoice, treatment)
    kind = {"promo_item": "promo", "working_meal_over_limit": "wages", "mixed": "hospitality", "staff_event_nonqualifying": "staff_event"}.get(
        classification, classification)
    refs = [SourceRef("invoices", invoice["id"])]
    refs += [SourceRef("received_facts", line["source_request"]) if line.get("source_request") else
             SourceRef("invoice_lines", line["id"]) for line in invoice.get("lines", [])]
    refs += [SourceRef(r["table"],r["id"]) for r in treatment.get("evidence_sources", [])]
    refs += [SourceRef("rules", r.split(" v")[0]) for r in treatment.get("rules", [])]
    return make_entry(entry_id or f"VCH-INV-{invoice['id']}", kind, event, invoice["entity"], amounts,
                      books, invoice.get("delivery_date") or invoice["invoice_date"], refs,
                      item=invoice["id"], reason=f"Computed {classification} treatment")


def invoice_amounts(invoice, treatment):
    """The signed amount per account that a computed treatment books for one invoice (debit +, credit -)."""
    classification = treatment["classification"]
    accounts = {"staff_event": "6440", "hospitality": "6400", "gift": "6420",
                "promo_item": "6480", "working_meal": "6490",
                "working_meal_over_limit": "6500", "staff_event_nonqualifying": "6440", "mixed": "6400"}
    if classification not in accounts:
        raise CannotDraft("classification requires a tax-manager decision")
    deductible = Decimal(str(treatment.get("deductible", 0)))
    nondeductible = Decimal(str(treatment.get("nondeductible", 0)))
    blocked = Decimal(str(treatment.get("vat_blocked", 0)))
    recovered = Decimal(str(treatment.get("vat_recoverable", 0)))
    amounts = {accounts[classification]: deductible}
    if classification in {"hospitality", "mixed"}:
        amounts["6410"] = nondeductible
        amounts[accounts[classification]] += blocked
    elif classification == "gift":
        amounts["6430"] = nondeductible
    else:
        # These computed deductible expenses already include their blocked VAT.
        if nondeductible:
            raise CannotDraft("unexpected non-deductible amount for this classification")
    program = Decimal(str(treatment.get("program_deductible", 0)))
    if program:
        amounts["6445"] = program
    amounts["1400"] = recovered
    amounts["1600"] = -Decimal(str(invoice["gross"]))
    return amounts


def flat_tax_entry(entry_id, event, entity, amount, regime, deductible, books, sources, item=None):
    if regime not in {"40", "37b"}:
        raise CannotDraft("unsupported flat tax regime")
    expense, liability = ("6470", "1750") if regime == "40" else (
        "6450" if deductible else "6460", "1760")
    value = Decimal(str(amount))
    return make_entry(entry_id, f"flat_tax_{regime}", event, entity,
                      {expense: value, liability: -value}, books, books.as_of.isoformat(), sources, item=item)


def correction_entry(entry_id, correction, event, entity, books, sources):
    kind = correction["type"]
    if kind == "payroll_delta":
        raise CannotDraft("a payroll delta is an export, not a journal entry")
    if kind not in {"vat_reversal", "reclass"}:
        raise CannotDraft("unknown correction type")
    source = correction.get("from_account") or ("1400" if kind == "vat_reversal" else None)
    target = correction.get("to_account") or ("6440" if kind == "vat_reversal" else None)
    if not source or not target or source == target:
        raise CannotDraft("a correction needs distinct source and destination accounts")
    value = Decimal(str(correction["amount"]))
    return make_entry(entry_id, kind, event, entity, {target: value, source: -value}, books,
                      books.as_of.isoformat(), sources, item=correction["ref"],
                      person=correction.get("person"), corrects=correction["ref"],
                      reason=correction.get("reason") or "Correction of previously booked treatment")
