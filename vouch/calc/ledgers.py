"""Per-person yearly ledgers and retroactive re-evaluation (spec §5.6): the spreading-over-time idea of
v1's vesting, for event tax.

A ledger is never a stored running total: it is derived on every run from the visible rows. Each run
computes the current answer for every person and item it touches, compares it with what was previously
booked (journal entries and payroll exports), and turns every difference into a correction dated in the
current open period:
  payroll_delta   an employee's taxable amount for an event changed (allowance moved, cost changed)
  vat_reversal    input VAT reclaimed earlier is now blocked (an event crossed EUR 110, a gift limit crossed)
  reclass         a gift (and its §37b tax) moved from deductible to non-deductible, or back
An event or gift with no booked results is new, not a correction.

Pure functions; amounts are Decimal; every result names its rules and policies."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from vouch.calc import eventtax as et
from vouch.money import ZERO


@dataclass(frozen=True)
class EventAttribution:
    """One staff event as it stands now: whether it qualifies, and what each employee is attributed."""
    event_id: str
    date: str
    qualifying: bool
    attributed: dict                    # employee -> attributed gross (POL-ROUND)


@dataclass
class Correction:
    type: str                           # payroll_delta | vat_reversal | reclass
    amount: Decimal
    ref: str                            # the original item: an event or a gift
    person: str | None = None           # the employee or recipient, for person-level corrections
    from_account: str | None = None
    to_account: str | None = None
    reason: str = ""
    rules: list = field(default_factory=list)

    def key(self) -> tuple:
        return (self.type, self.ref, self.from_account, self.to_account)


def optimize_allowances(attributions: list[EventAttribution], rules: et.Rules) -> dict[str, list[str]]:
    """POL-BV-OPT: each employee's allowance goes to the qualifying events (at most R-BV-MAX of them) that spare
    the most, ranked by min(attributed, 110); ties go to the earlier event (then the lower event ID)."""
    by_employee: dict[str, list[tuple]] = {}
    for event in attributions:
        if not event.qualifying:
            continue
        for employee, attributed in event.attributed.items():
            by_employee.setdefault((employee, event.date[:4]), []).append(
                (-et.exempt_amount(attributed, rules, event.date), event.date, event.event_id))
    out = {}
    for (employee, year), ranked in sorted(by_employee.items()):
        most = int(rules.value("R-BV-MAX", ranked[0][1]))
        out.setdefault(employee, []).extend(event_id for _, _, event_id in sorted(ranked)[:most])
    return out


@dataclass
class StaffLedger:
    """Every employee's staff-event rows for one year, derived from the visible events."""
    rows: dict                          # (employee, event) -> {"attributed", "allowance", "taxable"}
    assignment: dict                    # employee -> events with the allowance
    rules: list
    policies: list

    def taxable(self, employee: str, event: str) -> Decimal:
        row = self.rows.get((employee, event))
        return row["taxable"] if row else ZERO

    def events_of(self, employee: str) -> list[str]:
        return [ev for (emp, ev) in self.rows if emp == employee]


def staff_ledger(attributions: list[EventAttribution], rules: et.Rules) -> StaffLedger:
    """R-BV-ALLOW with POL-BV-OPT: taxable = attributed - 110 where the allowance is assigned (never below 0),
    the full attributed amount where it is not (a third event, or a non-qualifying event)."""
    assignment = optimize_allowances(attributions, rules)
    rows = {}
    for event in attributions:
        for employee, attributed in event.attributed.items():
            allowance = event.qualifying and event.event_id in assignment.get(employee, [])
            rows[(employee, event.event_id)] = {
                "attributed": attributed, "allowance": allowance,
                "taxable": et.taxable_after_allowance(attributed, allowance, rules, event.date),
                "qualifying": event.qualifying}
    on = max((e.date for e in attributions), default=None)
    cites = [rules.cite("R-BV-ALLOW", on), rules.cite("R-BV-MAX", on)] if on else []
    return StaffLedger(rows, assignment, cites, ["POL-BV-OPT"])


@dataclass
class PayrollChanges:
    """What a run changes on payroll: taxable amounts for events not booked before, and deltas for booked ones."""
    new: dict                           # (employee, event) -> taxable, events not booked before
    deltas: dict                        # (employee, event) -> current - previous, booked events
    corrections: list                   # one payroll_delta per booked event that changed
    reassigned: list                    # employees whose allowance moved off a booked event

    @property
    def new_total(self) -> Decimal:
        return sum(self.new.values(), ZERO)

    @property
    def delta_total(self) -> Decimal:
        return sum(self.deltas.values(), ZERO)


def payroll_changes(ledger: StaffLedger, previous: dict, booked_events: set, scope_events: set,
                    rules: et.Rules) -> PayrollChanges:
    """Compare the ledger with what payroll was told before (`previous`: (employee, event) -> taxable).
    Events in `scope_events` that were never booked are new; every booked event is diffed (a new event can
    move the allowance off an older one: POL-BV-OPT, 'changes produce payroll corrections')."""
    new, deltas = {}, {}
    for (employee, event), row in ledger.rows.items():
        if event in booked_events:
            delta = row["taxable"] - previous.get((employee, event), ZERO)
            if delta:
                deltas[(employee, event)] = delta
        elif event in scope_events and row["taxable"]:
            new[(employee, event)] = row["taxable"]
    for (employee, event), before in previous.items():
        if event in booked_events and (employee, event) not in ledger.rows and before:
            deltas[(employee, event)] = -before
    corrections, reassigned = [], set()
    for event in sorted({ev for _, ev in deltas}):
        amount = sum((d for (e, ev), d in deltas.items() if ev == event), ZERO)
        moved = sorted(e for (e, ev) in deltas if ev == event and (e, ev) in ledger.rows
                       and not ledger.rows[(e, ev)]["allowance"] and ledger.rows[(e, ev)]["qualifying"]
                       and len([x for x in ledger.events_of(e) if ledger.rows[(e, x)]["qualifying"]])
                       > int(rules.value("R-BV-MAX", None)))
        reassigned.update(moved)
        why = (f"allowance moved to other events for {len(moved)} employee(s) (POL-BV-OPT)" if moved
               else "taxable amounts changed with the event's cost or attendance")
        corrections.append(Correction("payroll_delta", amount, event, reason=why, rules=list(ledger.rules)))
    return PayrollChanges(new, deltas, corrections, sorted(reassigned))


def vat_correction(event_id: str, recoverable_now: bool, booked_reclaimed: Decimal, rules: et.Rules,
                   on: str) -> list[Correction]:
    """R-VAT-FIX: input VAT reclaimed for an event that is now blocked is reversed in the current open period."""
    if recoverable_now or booked_reclaimed <= 0:
        return []
    return [Correction("vat_reversal", booked_reclaimed, event_id,
                       reason="the cost per participant now exceeds EUR 110, so no input VAT is recoverable for the "
                              "whole event", rules=[rules.cite("R-BV-VAT", on), rules.cite("R-VAT-FIX", on)])]


@dataclass(frozen=True)
class BookedGift:
    """How a gift was booked before: deductible or not, VAT reclaimed, and its §37b tax and account."""
    gift_id: str
    recipient: str
    deductible: bool
    net: Decimal
    vat_reclaimed: Decimal
    flat_37b: Decimal
    flat_account: str | None


def gift_corrections(current: et.Result, booked: list[BookedGift], rules: et.Rules, on: str) -> list[Correction]:
    """R-GIFT-50 with R-VAT-FIX and R-37B-DED: when a recipient crosses the limit, every gift booked as deductible
    that year is reclassified (6420 -> 6430), its reclaimed VAT reversed, and its §37b tax moved (6450 -> 6460)."""
    out = []
    items = current.values.get("items", {})
    cites = [rules.cite("R-GIFT-50", on), rules.cite("R-VAT-FIX", on), rules.cite("R-37B-DED", on)]
    for gift in booked:
        now = items.get(gift.gift_id)
        if now is None or now["deductible"] == gift.deductible:
            continue
        if gift.deductible and not now["deductible"]:
            out.append(Correction("reclass", gift.net, gift.gift_id, gift.recipient, "6420", "6430",
                                  f"{gift.recipient}'s gifts this year now exceed the limit", cites))
            if gift.vat_reclaimed:
                out.append(Correction("vat_reversal", gift.vat_reclaimed, gift.gift_id, gift.recipient, "1400", "6430",
                                      "input VAT on a non-deductible gift is blocked", cites))
            if gift.flat_37b and gift.flat_account != "6460":
                out.append(Correction("reclass", gift.flat_37b, gift.gift_id, gift.recipient, "6450", "6460",
                                      "§37b tax follows the gift's deductibility (POL-37B-01)", cites))
        else:
            out.append(Correction("reclass", gift.net, gift.gift_id, gift.recipient, "6430", "6420",
                                  f"{gift.recipient}'s gifts this year are back within the limit", cites))
            if gift.flat_37b and gift.flat_account == "6460":
                out.append(Correction("reclass", gift.flat_37b, gift.gift_id, gift.recipient, "6460", "6450",
                                      "§37b tax follows the gift's deductibility (POL-37B-01)", cites))
    return out
