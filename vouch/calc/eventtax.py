"""The event tax engine (spec §6.1). Four separate questions for every event, answered by fixed rules:
how much the company may deduct, whether input VAT can be reclaimed, how much becomes each employee's
taxable pay, and whether a flat tax is due.

Pure functions: everything comes in as arguments (the rules too, with their versions), nothing is read
from anywhere else, every amount is Decimal, and rounding happens exactly where POL-ROUND says:
  per-participant cost is kept at full precision;
  attributed amount per employee = round(per_participant x (1 + companions));
  flat taxes on the summed base of a run: tax = round(base x rate), surcharge = round(tax x 0.055),
  church tax = round(tax x 0.07).
Every result names the rules (and versions) and company policies it applied. A policy-based outcome is
labelled as policy (POL-*), never as law (D20)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from vouch.money import ZERO, cents, fmt

HUNDRED = Decimal(100)


class RuleMissing(LookupError):
    """No version of a rule is valid on the date asked for."""


@dataclass(frozen=True)
class RuleVersion:
    rule_id: str
    version: int
    valid_from: str | None
    valid_to: str | None
    value: Decimal | None
    text: str = ""


class Rules:
    """The seeded rules table: every lookup picks the version valid on a date and says which it used."""

    def __init__(self, rows: list[dict]):
        self.versions: dict[str, list[RuleVersion]] = {}
        for r in rows:
            value = r.get("value")
            self.versions.setdefault(str(r["rule_id"]), []).append(RuleVersion(
                str(r["rule_id"]), int(r.get("version") or 1), r.get("valid_from") and str(r["valid_from"]),
                r.get("valid_to") and str(r["valid_to"]), Decimal(str(value)) if value not in (None, "") else None,
                str(r.get("text") or "")))

    def get(self, rule_id: str, on: date | str | None = None) -> RuleVersion:
        day = str(on)[:10] if on else None
        candidates = [v for v in self.versions.get(rule_id, [])
                      if day is None or ((v.valid_from or "0000") <= day and day <= (v.valid_to or "9999"))]
        if not candidates:
            raise RuleMissing(f"no version of {rule_id} is valid on {day}")
        return max(candidates, key=lambda v: v.version)

    def value(self, rule_id: str, on: date | str | None = None) -> Decimal:
        found = self.get(rule_id, on).value
        if found is None:
            raise RuleMissing(f"{rule_id} has no value")
        return found

    def cite(self, rule_id: str, on: date | str | None = None) -> str:
        """'R-BV-ALLOW v1': the rule and the version that applied."""
        return f"{rule_id} v{self.get(rule_id, on).version}"


@dataclass
class Result:
    """A computed answer and what it rests on."""
    values: dict
    rules: list = field(default_factory=list)          # 'R-... vN'
    policies: list = field(default_factory=list)       # 'POL-...'
    notes: list = field(default_factory=list)

    def cites(self) -> list[str]:
        return [r.split(" v")[0] for r in self.rules] + self.policies

    def to_dict(self) -> dict:
        def show(v):
            if isinstance(v, Decimal):
                return fmt(v) if v == cents(v) else str(v)
            if isinstance(v, dict):
                return {k: show(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [show(x) for x in v]
            return v
        return {**{k: show(v) for k, v in self.values.items()}, "rules": self.rules, "policies": self.policies,
                "notes": self.notes}


# -- staff events (Betriebsveranstaltung) ---------------------------------------------------------
def allocate_staff_event(total_gross: Decimal, attendees: list[dict], rules: Rules, on: str) -> Result:
    """R-BV-COST: all costs incl. VAT divided by the actual attendees incl. companions; each employee is
    attributed round(per_participant x (1 + their companions)) (POL-ROUND). Attendees are dicts with
    person_id, person_kind and, for companions, host_employee."""
    participants = len(attendees)
    if participants == 0:
        raise ValueError("no attendees: attendance is never estimated")
    per_participant = Decimal(total_gross) / participants
    companions: dict[str, int] = {}
    for a in attendees:
        if a["person_kind"] == "companion":
            companions[a["host_employee"]] = companions.get(a["host_employee"], 0) + 1
    attributed = {a["person_id"]: cents(per_participant * (1 + companions.get(a["person_id"], 0)))
                  for a in attendees if a["person_kind"] == "employee"}
    return Result({"total_gross": Decimal(total_gross), "participants": participants,
                   "per_participant": per_participant, "per_participant_rounded": cents(per_participant),
                   "attributed": attributed, "companions": companions},
                  [rules.cite("R-BV-COST", on)], ["POL-ROUND"])


def staff_event_vat_test(per_participant: Decimal, rules: Rules, on: str) -> Result:
    """R-BV-VAT (Freigrenze): input VAT recoverable only if the gross cost per participant, rounded to the cent,
    is at most EUR 110.00; otherwise none for the whole event. Per participant incl. companions (POL-BV-VATBASIS)."""
    limit = rules.value("R-BV-VAT", on)
    rounded = cents(per_participant)
    return Result({"per_participant": rounded, "limit": limit, "recoverable": rounded <= limit},
                  [rules.cite("R-BV-VAT", on)], ["POL-BV-VATBASIS"])


def taxable_after_allowance(attributed: Decimal, allowance: bool, rules: Rules, on: str) -> Decimal:
    """R-BV-ALLOW: with the allowance, only the excess over EUR 110 is taxable; without it, all of it."""
    if not allowance:
        return attributed
    return max(ZERO, attributed - rules.value("R-BV-ALLOW", on))


def exempt_amount(attributed: Decimal, rules: Rules, on: str) -> Decimal:
    """What the allowance spares on one event: min(attributed, 110) (POL-BV-OPT ranks events by this)."""
    return min(attributed, rules.value("R-BV-ALLOW", on))


def qualifies_as_staff_event(open_to_all: bool, employees: int, companions: int, others: int, rules: Rules,
                             on: str) -> Result:
    """R-BV-DEF: open to all staff of the company or an org unit, and employees plus companions are more
    than half of the attendees."""
    total = employees + companions + others
    share = Decimal(employees + companions) / total if total else ZERO
    majority = share > rules.value("R-BV-DEF", on)
    return Result({"open_to_all": open_to_all, "staff_share": share, "staff_majority": majority,
                   "qualifies": open_to_all and majority}, [rules.cite("R-BV-DEF", on)])


# -- hospitality (Bewirtung) ----------------------------------------------------------------------
def hospitality_split(net: Decimal, rules: Rules, on: str) -> Result:
    """R-BEW-70: deductible = round(net x 0.70), non-deductible = net - deductible."""
    deductible = cents(Decimal(net) * rules.value("R-BEW-70", on))
    return Result({"net": Decimal(net), "deductible": deductible, "nondeductible": Decimal(net) - deductible},
                  [rules.cite("R-BEW-70", on)])


GENERIC_OCCASIONS = {"gesch\u00e4ftsessen", "gesch?ftsessen", "", "geschäftsessen", "geschaeftsessen", "bewirtung", "essen", "business meal", "business dinner",
                     "dinner", "lunch", "meeting", "besprechung", "arbeitsessen", "kundenessen", "customer dinner"}


def hospitality_docs(occasion: str | None, participants_named: bool, host: str | None, gross: Decimal,
                     date_known: bool, place_known: bool, rules: Rules, on: str) -> Result:
    """R-BEW-DOC: date, place, host, every participant's name and a specific occasion; an invoice over
    EUR 250 gross must name the host. Returns what is missing."""
    missing = []
    if not date_known:
        missing.append("date")
    if not place_known:
        missing.append("place")
    if not participants_named:
        missing.append("participants")
    if str(occasion or "").strip().lower() in GENERIC_OCCASIONS:
        missing.append("specific occasion")
    if not host:
        missing.append("host" + (" (required on an invoice over EUR " + fmt(rules.value("R-BEW-DOC", on)) + ")"
                                 if Decimal(gross) > rules.value("R-BEW-DOC", on) else ""))
    return Result({"complete": not missing, "missing": missing}, [rules.cite("R-BEW-DOC", on)])


# -- gifts (Geschenke) ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Gift:
    id: str
    date: str
    net: Decimal
    vat: Decimal
    gross: Decimal
    recipient: str


def gift_test_value(gift: Gift) -> Decimal:
    """R-GIFT-50: a gift counts at net when its input VAT is recoverable in principle (the invoice shows VAT),
    else at gross. The §37b flat tax never counts (POL-37B-01)."""
    return gift.net if gift.vat > 0 else gift.gross


def gift_ledger(gifts: list[Gift], year: int, rules: Rules) -> Result:
    """R-GIFT-50 / R-GIFT-VER: one recipient's gifts in a year. At or under the limit (EUR 50 from 2024,
    EUR 35 before) every gift is deductible with VAT recoverable; over it, every gift that year is
    non-deductible at gross with its VAT blocked."""
    items = sorted((g for g in gifts if int(g.date[:4]) == year), key=lambda g: (g.date, g.id))
    if not items:
        return Result({"total": ZERO, "limit": None, "deductible": True, "items": {}}, [])
    on = f"{year}-12-31"
    limit = rules.value("R-GIFT-VER", items[-1].date)
    total = sum((gift_test_value(g) for g in items), ZERO)
    deductible = total <= limit
    treatment = {g.id: {"deductible": deductible, "test_value": gift_test_value(g),
                        "deductible_amount": g.net if deductible else ZERO,
                        "nondeductible_amount": ZERO if deductible else g.gross,
                        "vat_recoverable": g.vat if deductible else ZERO, "vat_blocked": ZERO if deductible else g.vat}
                 for g in items}
    return Result({"recipient": items[0].recipient, "year": year, "total": total, "limit": limit,
                   "deductible": deductible, "items": treatment},
                  [rules.cite("R-GIFT-50", on), rules.cite("R-GIFT-VER", items[-1].date), rules.cite("R-GIFT-REC", on)],
                  ["POL-37B-01"])


def is_promo_item(unit_net: Decimal, rules: Rules, on: str) -> bool:
    """R-PROMO: an item of at most EUR 10.00 net per unit needs no recipient record and no §37b."""
    return Decimal(unit_net) <= rules.value("R-PROMO", on)


# -- flat taxes -------------------------------------------------------------------------------------
def _flat(base: Decimal, rate_rule: str, rules: Rules, on: str) -> Result:
    rate = rules.value(rate_rule, on)
    tax = cents(Decimal(base) * rate)
    soli = cents(tax * rules.value("TAX-SOLI", on))
    church = cents(tax * rules.value("TAX-KIST", on))
    return Result({"base": Decimal(base), "rate": rate, "tax": tax, "surcharge": soli, "church_tax": church,
                   "total": tax + soli + church},
                  [rules.cite(rate_rule, on), rules.cite("TAX-SOLI", on), rules.cite("TAX-KIST", on)], ["POL-ROUND"])


def flat_tax_40(base: Decimal, rules: Rules, on: str) -> Result:
    """§40(2) flat payroll tax on the run's summed taxable staff-event benefits: 25% + 5.5% + 7% of the tax."""
    result = _flat(base, "R-BV-FLAT", rules, on)
    result.policies.append("POL-BV-FLAT")
    return result


def flat_tax_37b(base: Decimal, rules: Rules, on: str) -> Result:
    """§37b flat tax on the run's summed gross benefits to non-employees: 30% + 5.5% + 7% of the tax."""
    return _flat(base, "R-37B-RATE", rules, on)


def flat_tax_37b_available(recipient_year_gross: Decimal, rules: Rules, on: str) -> Result:
    """R-37B-CAP: §37b is not available for a recipient above EUR 10,000 gross in the year."""
    cap = rules.value("R-37B-CAP", on)
    return Result({"recipient_year_gross": Decimal(recipient_year_gross), "cap": cap,
                   "available": Decimal(recipient_year_gross) <= cap}, [rules.cite("R-37B-CAP", on)])


def share_by_headcount(amount: Decimal, part: int, whole: int) -> Decimal:
    """POL-BEW-02: the business partners' pro-rata share of a customer-event program, by headcount."""
    return cents(Decimal(amount) * part / whole)


# -- working meals (Arbeitsessen) ----------------------------------------------------------------
def working_meal_test(total_gross: Decimal, persons: int, rules: Rules, on: str) -> Result:
    """R-AE-60: a meal during an exceptional assignment is not pay up to EUR 60.00 gross per person; above,
    its full value is pay."""
    if persons <= 0:
        raise ValueError("no attendees: attendance is never estimated")
    per_person = cents(Decimal(total_gross) / persons)
    limit = rules.value("R-AE-60", on)
    return Result({"per_person": per_person, "limit": limit, "within_limit": per_person <= limit},
                  [rules.cite("R-AE-60", on)])


# -- invoice arithmetic -----------------------------------------------------------------------------
def vat_arithmetic(lines: list[dict]) -> list[str]:
    """TAX-VAT: VAT is never assumed; it is read from the invoice and its arithmetic checked: net + vat = gross,
    and vat = round(net x vat_rate) where a rate is shown. Returns every line that does not add up."""
    problems = []
    for line in lines:
        try:
            net, vat, gross = (Decimal(str(line[k])) for k in ("net", "vat", "gross"))
        except (KeyError, TypeError, ArithmeticError, ValueError):
            problems.append(f"{line.get('id')}: net, VAT or gross missing")
            continue
        if net + vat != gross:
            problems.append(f"{line.get('id')}: net {fmt(net)} + VAT {fmt(vat)} is not gross {fmt(gross)}")
        rate = line.get("vat_rate")
        if rate not in (None, "") and cents(net * Decimal(str(rate))) != vat:
            problems.append(f"{line.get('id')}: VAT {fmt(vat)} is not {rate} of net {fmt(net)}")
    return problems
