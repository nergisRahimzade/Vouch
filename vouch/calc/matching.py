"""The matching engine (spec §6.1). For every invoice line it computes what Anthropic owes from the
signed clauses and the visible evidence, compares it with what was billed, and splits the invoice into
an approved, a held and a disputed amount, each difference with its reason.

Pure function: everything comes in through MatchInput, nothing is read from anywhere else, and every
amount is Decimal rounded half up to the cent. The source of truth for each fact follows spec §5.3.

Two expected amounts are kept per line:
  full    what we owe once every open question is answered our way (missing evidence arrives, ...)
  strict  what we owe on the evidence visible now
Each is capped at what the line bills (never approve more than the invoice asks: POL-AP-03). Then
  approved = sum(strict), held = sum(full - strict), disputed = billed - sum(full)."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from vouch.db.accounts import CATEGORY_ACCOUNT
from vouch.money import ZERO, cents, fmt

CENT = Decimal("0.01")
INFO = {"possible_underbilling", "approval_aggregated", "vendor_enhanced_review"}
# Clause types that price a line on their own.
PRICED = {"venue_hire", "meeting_space", "fixed_fee", "fixed_price", "av_package", "day_rate", "crew_overtime",
          "venue_overtime", "per_head_price", "unit_items", "lodging_block", "cancellation", "change_order", "deposit"}
# Where each priced clause type's cost goes; None: the vendor's category decides.
TYPE_ACCOUNT = {"venue_hire": "6100", "meeting_space": "6100", "venue_overtime": "6100", "per_head_price": "6110",
                "av_package": "6120", "day_rate": "6120", "crew_overtime": "6120", "unit_items": "6130",
                "lodging_block": "6140"}
TYPE_CATEGORY = {"venue_hire": "venue", "venue_overtime": "venue", "per_head_price": "catering", "guarantee": "catering",
                 "av_package": "av", "day_rate": "av", "crew_overtime": "av", "unit_items": "merchandise",
                 "lodging_block": "lodging", "attrition": "lodging"}
MAIN = {"venue_hire", "per_head_price", "lodging_block", "av_package", "fixed_fee"}
APPLIED_TO = {"lodging": "lodging_block", "venue": "venue_hire", "catering": "per_head_price", "food": "per_head_price",
              "av": "av_package"}
DATE_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")


class MalformedInvoice(ValueError):
    pass


def dec(value, default: Decimal | None = ZERO) -> Decimal:
    if value is None or value == "":
        if default is None:
            raise MalformedInvoice("a required amount is blank")
        return default
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise MalformedInvoice(f"{value!r} is not an amount") from error


def jsonish(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass
class MatchInput:
    invoice: dict                       # invoice row with "lines": [invoice_lines rows]
    contract: dict | None
    clauses: dict                       # clause_id -> {"clause_id", "type", "params": dict, "text", "contract"}
    po: dict | None                     # purchase order row with "lines": [po_lines rows]
    evidence: list                      # evidence rows for the event, "value" as a dict
    deposits: list                      # paid deposits on this contract: {"clause_ref", "invoice", "net", "vat", "gross"}
    credited: set = field(default_factory=set)       # deposit clauses already credited on an earlier invoice
    policies: dict = field(default_factory=dict)     # policy id -> value text
    event: dict | None = None
    vendor: dict = field(default_factory=dict)
    ops_people: set = field(default_factory=set)     # who signs for event ops
    owner: str | None = None                         # the event owner


@dataclass
class Issue:
    flag: str
    kind: str                           # dispute | hold | info
    amount: Decimal
    reason: str
    clause_ids: list = field(default_factory=list)
    evidence_ids: list = field(default_factory=list)
    line_id: str | None = None
    request: dict | None = None         # {"to_role": ..., "about": ...}: who can settle a hold

    def to_dict(self) -> dict:
        return {**asdict(self), "amount": fmt(self.amount)}


@dataclass
class LineResult:
    line_id: str | None                 # None: a line the invoice should have carried (a missing deposit credit)
    description: str
    kind: str
    billed: Decimal
    full: Decimal
    strict: Decimal
    rule: str
    source_of_truth: str
    clause_ids: list = field(default_factory=list)
    evidence_ids: list = field(default_factory=list)
    account: str | None = None
    detail: str = ""
    food: bool = False                  # a per-head food line (service charge and tax may be charged on it)
    inclusive: bool = False             # a fixed price inclusive of service and tax
    vat_pct: Decimal | None = None      # VAT the line's own clause sets (UK venue hire)
    is_vat: bool = False                # a VAT line (reclaimable for ENT-UK: account 1400)

    @property
    def expected(self) -> Decimal:
        return self.full

    @property
    def difference(self) -> Decimal:
        return self.billed - self.full

    def to_dict(self) -> dict:
        return {"line_id": self.line_id, "description": self.description, "kind": self.kind,
                "billed": fmt(self.billed), "expected": fmt(self.full), "approved_now": fmt(self.strict),
                "difference": fmt(self.difference), "rule": self.rule, "source_of_truth": self.source_of_truth,
                "clause_ids": self.clause_ids, "evidence_ids": self.evidence_ids, "account": self.account,
                "detail": self.detail}


@dataclass
class MatchResult:
    invoice_id: str
    currency: str
    invoice_kind: str                   # deposit | final
    billed: Decimal
    lines: list
    issues: list
    approved: Decimal
    held: Decimal
    disputed: Decimal
    warnings: list = field(default_factory=list)

    def flags(self) -> list[str]:
        return sorted({i.flag for i in self.issues})

    def to_dict(self) -> dict:
        return {"invoice": self.invoice_id, "currency": self.currency, "invoice_kind": self.invoice_kind,
                "billed": fmt(self.billed), "approved": fmt(self.approved), "held": fmt(self.held),
                "disputed": fmt(self.disputed), "flags": self.flags(), "lines": [l.to_dict() for l in self.lines],
                "issues": [i.to_dict() for i in self.issues], "warnings": self.warnings}


# -- helpers -------------------------------------------------------------------------------
def _time(text: str) -> tuple[int, int]:
    hours, minutes = str(text).strip()[:5].split(":")
    return int(hours), int(minutes)


def _hours_between(start: str, end: str) -> Decimal:
    """Hours from start to end on a clock (HH:MM); an end before the start is after midnight."""
    (h1, m1), (h2, m2) = _time(start), _time(end)
    minutes = (h2 * 60 + m2) - (h1 * 60 + m1)
    if minutes < 0:
        minutes += 24 * 60
    return Decimal(minutes) / 60


def round_up_to(hours: Decimal, increment: Decimal) -> Decimal:
    """Hours rounded up to whole increments: 0 stays 0, one minute over is a full increment."""
    if hours <= 0:
        return ZERO
    return (hours / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def venue_overtime_hours(ended_at: str, after: str, increment: Decimal) -> Decimal:
    """Overtime from the `after` time to the load-out end, in increments rounded up. An end in the
    early hours belongs to the night before (00:47 on the 13th is 1h47m after 23:00 on the 12th)."""
    ended = datetime.fromisoformat(str(ended_at)[:16])
    h, m = _time(after)
    anchor = ended.replace(hour=h, minute=m, second=0, microsecond=0)
    if ended < anchor and ended.hour < 12:
        anchor -= timedelta(days=1)
    minutes = max(0, int((ended - anchor).total_seconds() // 60))
    return round_up_to(Decimal(minutes) / 60, increment)


def count_from(value: dict, preferred: tuple[str, ...]) -> Decimal | None:
    """The count an evidence row reports: a preferred key first, else its only number."""
    for key in preferred:
        if key in value and value[key] not in (None, ""):
            return dec(value[key])
    numbers = [v for k, v in value.items() if k not in ("vendor", "item") and isinstance(v, (int, Decimal))]
    return dec(numbers[0]) if len(numbers) == 1 else None


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _pct(value) -> Decimal:
    """A percentage given as a fraction (0.20) or as a number of percent (20)."""
    p = dec(value)
    return p / 100 if p > 1 else p


class Matcher:
    def __init__(self, data: MatchInput):
        self.d = data
        self.inv = data.invoice
        self.lines = sorted(data.invoice.get("lines") or [], key=lambda l: (int(l.get("line_no") or 0), str(l["id"])))
        self.issues: list[Issue] = []
        self.results: list[LineResult] = []
        self.warnings: list[str] = []
        self.contract_types = {c["type"] for c in data.clauses.values()}

    # -- lookups ----------------------------------------------------------------------------
    def clause_of_type(self, *types: str) -> dict | None:
        found = [c for c in self.d.clauses.values() if c["type"] in types]
        return found[0] if found else None

    def category_account(self) -> str | None:
        category = self.d.vendor.get("category")
        if not category:
            categories = {TYPE_CATEGORY[t] for t in self.contract_types if t in TYPE_CATEGORY}
            category = categories.pop() if len(categories) == 1 else None
        return CATEGORY_ACCOUNT.get(category)

    def account_for(self, clause: dict | None) -> str | None:
        if clause is None:
            return self.category_account()
        if clause["type"] == "deposit":
            applied = APPLIED_TO.get(str(clause["params"].get("applied_to", "")).lower())
            if applied and applied in TYPE_ACCOUNT:
                return TYPE_ACCOUNT[applied]
        return TYPE_ACCOUNT.get(clause["type"]) or self.category_account()

    def clause_for(self, line: dict) -> dict | None:
        ref = str(line.get("clause_ref") or "")
        base = ref.split(".")[0]
        if base in self.d.clauses:
            return self.d.clauses[base]
        if line.get("po_line") and self.d.po:
            po_line = next((p for p in self.d.po.get("lines", []) if p["id"] == line["po_line"]), None)
            if po_line and str(po_line.get("clause_ref") or "").split(".")[0] in self.d.clauses:
                return self.d.clauses[str(po_line["clause_ref"]).split(".")[0]]
        kind = line.get("kind") or "item"
        if kind in ("service_charge", "tax", "vat", "deposit_credit", "deposit"):
            return None
        words = _norm(line.get("description")) + _norm(line.get("item"))
        priced = [c for c in self.d.clauses.values() if c["type"] in PRICED - {"deposit", "change_order"}]
        for clause in priced:
            names = [_norm(clause["params"].get("desc")), _norm(clause["params"].get("meal"))]
            names += [_norm(k) for k in (clause["params"].get("items") or {})]
            if any(n and (n in words or words in n) for n in names if len(n) > 3):
                return clause
        if len(priced) == 1 and not self.d.po:
            return priced[0]
        if self.d.po:
            on_po = {str(p.get("clause_ref") or "").split(".")[0] for p in self.d.po.get("lines", [])}
            candidates = [c for c in priced if c["clause_id"] in on_po]
            if len(candidates) == 1:
                return candidates[0]
        return None

    def evidence(self, *types: str, vendor: bool = False) -> list[dict]:
        rows = [e for e in self.d.evidence if e["type"] in types]
        if vendor:
            mine = [e for e in rows if jsonish(e["value"]).get("vendor") == self.inv.get("vendor")]
            unlabelled = [e for e in rows if "vendor" not in jsonish(e["value"])]
            rows = mine or unlabelled
        return rows

    def on_date(self, rows: list[dict], day: str | None) -> list[dict]:
        if day:
            same = [e for e in rows if str(e.get("date"))[:10] == day]
            if same:
                return same
            return []
        return rows

    def signed_by_ops(self, row: dict) -> bool:
        return bool(row.get("signed_by")) and row["signed_by"] in self.d.ops_people

    def add(self, result: LineResult) -> LineResult:
        result.full = min(cents(result.full), result.billed)
        result.strict = min(cents(result.strict), result.full)
        self.results.append(result)
        return result

    def issue(self, flag: str, kind: str, amount: Decimal, reason: str, line: LineResult | None,
              request: dict | None = None, clause_ids=None, evidence_ids=None) -> None:
        if kind != "info" and amount <= 0:
            return
        self.issues.append(Issue(flag, kind, cents(amount), reason,
                                 list(clause_ids if clause_ids is not None else (line.clause_ids if line else [])),
                                 list(evidence_ids if evidence_ids is not None else (line.evidence_ids if line else [])),
                                 line.line_id if line else None, request))

    def underbilled(self, line: LineResult, uncapped: Decimal, why: str) -> None:
        if cents(uncapped) > line.billed + CENT / 2:
            self.issue("possible_underbilling", "info", cents(uncapped) - line.billed,
                       f"{why}; the invoice is paid as billed (POL-AP-03)", line)

    # -- run ----------------------------------------------------------------------------------
    def run(self) -> MatchResult:
        currency = str(self.inv.get("currency") or self.d.vendor.get("currency") or "")
        billed_lines = [dec(l.get("amount"), None) for l in self.lines]
        billed = sum(billed_lines, ZERO)
        total = self.inv.get("total")
        if not self.lines:
            raise MalformedInvoice("the invoice has no lines")
        if total not in (None, "") and dec(total) != billed:
            raise MalformedInvoice(f"the lines add up to {fmt(billed)} but the invoice total is {fmt(dec(total))}")
        roles = {"base": [], "svc": [], "tax": [], "credit": []}
        for line in self.lines:
            clause = self.clause_for(line)
            kind = line.get("kind") or "item"
            amount = dec(line.get("amount"))
            if kind == "deposit_credit" or (amount < 0 and (clause or {}).get("type") == "deposit"):
                roles["credit"].append((line, clause))
            elif kind == "service_charge" or (clause or {}).get("type") == "service_charge":
                roles["svc"].append((line, clause))
            elif kind in ("tax", "vat") or (clause or {}).get("type") in ("sales_tax", "vat"):
                roles["tax"].append((line, clause))
            else:
                roles["base"].append((line, clause))
        deposit_invoice = bool(roles["base"]) and all((c or {}).get("type") == "deposit" or l.get("kind") == "deposit"
                                                      for l, c in roles["base"])
        self.base = [self.base_line(line, clause) for line, clause in roles["base"]]
        self.svc = [self.service_line(line, clause) for line, clause in roles["svc"]]
        self.tax = [self.tax_line(line, clause) for line, clause in roles["tax"]]
        if not deposit_invoice:
            self.credits(roles["credit"])
        else:
            for line, clause in roles["credit"]:
                self.plain(line, clause, ZERO, "a deposit invoice carries no deposit credit")
        approved = sum((r.strict for r in self.results), ZERO)
        full = sum((r.full for r in self.results), ZERO)
        return MatchResult(str(self.inv["id"]), currency, "deposit" if deposit_invoice else "final", billed,
                           self.results, self.issues, approved, full - approved, billed - full, self.warnings)

    def make(self, line: dict | None, clause: dict | None, rule: str, truth: str, billed: Decimal | None = None,
             evidence_ids=None, detail: str = "") -> LineResult:
        clause_ids = [clause["clause_id"]] if clause else []
        return LineResult(line_id=str(line["id"]) if line else None,
                          description=str((line or {}).get("description") or (line or {}).get("item") or ""),
                          kind=str((line or {}).get("kind") or "item"),
                          billed=dec((line or {}).get("amount")) if billed is None else billed,
                          full=ZERO, strict=ZERO, rule=rule, source_of_truth=truth, clause_ids=clause_ids,
                          evidence_ids=list(evidence_ids or []), account=self.account_for(clause), detail=detail)

    def plain(self, line: dict, clause: dict | None, expected: Decimal, rule: str) -> LineResult:
        r = self.make(line, clause, rule, "contract clause")
        r.full = r.strict = expected
        r = self.add(r)
        self.issue("rate_above_contract", "dispute", r.billed - r.full, f"{r.description or 'line'}: {rule}", r)
        return r

    # -- base lines -----------------------------------------------------------------------------
    def base_line(self, line: dict, clause: dict | None) -> LineResult:
        kind = (clause or {}).get("type")
        handler = {"per_head_price": self.per_head, "fixed_price": self.fixed, "venue_hire": self.fixed,
                   "meeting_space": self.fixed, "fixed_fee": self.fixed, "av_package": self.fixed,
                   "day_rate": self.day_rate, "crew_overtime": self.crew_overtime,
                   "venue_overtime": self.venue_overtime, "unit_items": self.merchandise,
                   "lodging_block": self.lodging, "cancellation": self.cancellation,
                   "change_order": self.change_order, "deposit": self.deposit_charge}.get(kind)
        if handler is None:
            return self.change_order(line, clause)
        return handler(line, clause)

    def qty_price(self, line: dict) -> tuple[Decimal, Decimal]:
        qty = dec(line.get("qty"), Decimal(1))
        amount = dec(line.get("amount"))
        price = dec(line.get("unit_price")) if line.get("unit_price") not in (None, "") else \
            (amount / qty if qty else amount)
        return qty, price

    def service_date(self, line: dict, clause: dict | None) -> str | None:
        if line.get("service_date"):
            return str(line["service_date"])[:10]
        for key in ("meal", "desc"):
            found = DATE_IN_TEXT.search(str((clause or {}).get("params", {}).get(key, "")))
            if found:
                return found.group(0)
        return None

    def per_head(self, line: dict, clause: dict) -> LineResult:
        """Catering per head: billed count = max(guarantee, verified served), never more than invoiced."""
        n, unit = self.qty_price(line)
        price = dec(clause["params"].get("price_per_head"))
        guarantee_clause = self.clause_of_type("guarantee")
        guarantee = dec((guarantee_clause or {}).get("params", {}).get("guaranteed_count"), ZERO)
        staff_clause = self.clause_of_type("staff_count")
        staff_counts = bool((staff_clause or {}).get("params", {}).get("counts_toward_headcount"))
        day = self.service_date(line, clause)
        event_days = self.event_days()
        if day is None and len(event_days) == 1:
            day = event_days[0]
        signed = [e for e in self.on_date(self.evidence("signed_meal_count"), day) if self.signed_by_ops(e)]
        badges = self.on_date(self.evidence("badge_scans"), day)
        roster = self.on_date(self.evidence("staff_meal_roster"), day)
        clause_ids = [clause["clause_id"]] + ([guarantee_clause["clause_id"]] if guarantee_clause else [])
        served, used, truth = None, [], "vendor's count (no Anthropic evidence)"
        if len(signed) == 1:
            served = count_from(jsonish(signed[0]["value"]), ("meals", "count", "served"))
            used, truth = [signed[0]["id"]], "meal count signed by event ops"
        elif len(badges) == 1:
            served = count_from(jsonish(badges[0]["value"]), ("lunch_attendees", "attendees", "count", "scans"))
            used, truth = [badges[0]["id"]], "badge scans"
            if staff_counts and len(roster) == 1:
                staff = count_from(jsonish(roster[0]["value"]), ("staff_meals", "staff", "count"))
                served = (served or ZERO) + (staff or ZERO)
                used.append(roster[0]["id"])
                truth = "badge scans plus staff roster (the contract counts staff)"
                clause_ids.append(staff_clause["clause_id"])
        elif len(signed) > 1 or len(badges) > 1:
            self.warnings.append(f"line {line['id']}: more than one meal count for {day or 'the event'}; "
                                 "the line's service date is needed to pick one")
        r = self.make(line, clause, "billed count = max(guarantee, verified served), capped at invoiced count; "
                      "food = count x contract price", truth, evidence_ids=used)
        r.clause_ids = clause_ids
        r.food = True
        if served is None:
            # Nothing Anthropic controls verifies the count: pay up to the guarantee, hold the rest for evidence.
            verified = min(guarantee, n)
            r.full = n * price
            r.strict = verified * price
            r.detail = f"invoiced {n}, guarantee {guarantee}, no signed meal count or badge scans"
            r = self.add(r)
            self.issue("missing_evidence", "hold", r.full - r.strict,
                       f"no signed meal count or badge scans to verify {n - verified} meals above the guarantee "
                       f"of {guarantee}", r, request={"to_role": "event_ops", "about": "signed_meal_count"})
        else:
            billed_count = min(max(guarantee, served), n)
            r.full = r.strict = billed_count * price
            r.detail = f"invoiced {n} at {fmt(unit)}; served {served} ({truth}); guarantee {guarantee}; " \
                       f"billed count {billed_count} at {fmt(price)}"
            r = self.add(r)
            self.issue("rate_above_contract", "dispute", n * max(ZERO, unit - price),
                       f"billed {fmt(unit)} per head against the contract price {fmt(price)}", r)
            self.issue("headcount_overbilled", "dispute", (n - billed_count) * min(unit, price),
                       f"billed {n} meals; {truth} shows {served} and the guarantee is {guarantee}, so "
                       f"{billed_count} are billable", r)
            if served > n:
                self.issue("possible_underbilling", "info", (served - n) * price,
                           f"{truth} shows {served} meals but only {n} were invoiced", r)
            self.underbilled(r, billed_count * price, f"billed {fmt(unit)} per head, below the contract {fmt(price)}")
        return r

    def event_days(self) -> list[str]:
        event = self.d.event or {}
        if not event.get("start_date"):
            return []
        start, end = date.fromisoformat(str(event["start_date"])[:10]), date.fromisoformat(str(event["end_date"])[:10])
        return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]

    def fixed(self, line: dict, clause: dict) -> LineResult:
        """A fixed amount from the contract (venue hire, package, meeting space, inclusive catering)."""
        params = clause["params"]
        amount = dec(params.get("fixed", params.get("net", params.get("amount"))))
        others = [r for r in self.results if clause["clause_id"] in r.clause_ids and r.line_id]
        already = sum((r.full for r in others), ZERO)
        r = self.make(line, clause, "fixed amount per the contract" + (", inclusive of service and tax"
                                                                        if params.get("inclusive") else ""),
                      "contract clause")
        r.full = r.strict = max(ZERO, amount - already)
        r.vat_pct = _pct(params["vat_pct"]) if params.get("vat_pct") not in (None, "") else None
        r.inclusive = bool(params.get("inclusive"))
        r = self.add(r)
        self.issue("rate_above_contract", "dispute", r.billed - r.full,
                   f"billed {fmt(r.billed)} against the contract amount {fmt(amount)}", r)
        self.underbilled(r, amount - already, f"billed below the contract amount {fmt(amount)}")
        return r

    def deposit_charge(self, line: dict, clause: dict) -> LineResult:
        """A deposit invoice: the deposit the clause sets (net of VAT when the clause shows VAT)."""
        params = clause["params"]
        amount = dec(params.get("net", params.get("amount")))
        r = self.make(line, clause, "deposit per the contract", "contract clause")
        r.full = r.strict = amount
        r = self.add(r)
        self.issue("rate_above_contract", "dispute", r.billed - r.full,
                   f"deposit billed {fmt(r.billed)} against {fmt(amount)} in the contract", r)
        return r

    def day_rate(self, line: dict, clause: dict) -> LineResult:
        """Day rates only for days on the PO."""
        rate = dec(clause["params"].get("rate"))
        days = clause["params"].get("days") or {}
        n, unit = self.qty_price(line)
        ref = str(line.get("clause_ref") or "")
        on_po = {str(p.get("clause_ref")) for p in (self.d.po or {}).get("lines", [])}
        po_days = {name: str(day) for name, day in days.items() if f"{clause['clause_id']}.{name}" in on_po}
        if "." in ref:
            wanted = [ref.split(".", 1)[1]]
        elif line.get("service_date"):
            wanted = [name for name, day in days.items() if str(day) == str(line["service_date"])[:10]] or \
                     [str(line["service_date"])[:10]]
        else:
            words = _norm(line.get("description"))
            wanted = [name for name in days if _norm(name) in words]
        if not wanted:
            billable = min(n, Decimal(len(po_days)))
        else:
            billable = min(n, Decimal(sum(1 for w in wanted if w in po_days)))
        r = self.make(line, clause, "day rate x days on the PO", "contract clause and purchase order")
        r.full = r.strict = billable * rate
        r.detail = f"billed {n} day(s) at {fmt(unit)}; days on the PO: {', '.join(sorted(po_days)) or 'none'}"
        extra = (n - billable) * min(unit, rate)
        if extra > 0:
            r.full = billable * rate + extra
        r = self.add(r)
        self.issue("rate_above_contract", "dispute", n * max(ZERO, unit - rate),
                   f"billed {fmt(unit)} per day against {fmt(rate)}", r)
        if extra > 0:
            co = self.clause_of_type("change_order")
            self.issue("unapproved_change_order", "hold", r.full - r.strict,
                       f"{n - billable} day(s) billed that are not on the PO; billable only with a signed change order",
                       r, request={"to_role": "event_owner", "about": "signed_change_order"},
                       clause_ids=r.clause_ids + ([co["clause_id"]] if co else []))
        return r

    def crew_overtime(self, line: dict, clause: dict) -> LineResult:
        """Crew overtime: rate x crew x hours beyond the call length, from the crew log."""
        params = clause["params"]
        rate = dec(params.get("rate_per_crew_hour"))
        call = dec(params.get("call_length_hours"))
        detail = jsonish(line.get("detail"))
        n, unit = self.qty_price(line)
        if detail.get("crew") and detail.get("hours"):
            n = dec(detail["crew"]) * dec(detail["hours"])
            unit = dec(line.get("amount")) / n if n else unit
        logs = self.evidence("crew_log", vendor=True)
        if line.get("service_date"):
            logs = self.on_date(logs, str(line["service_date"])[:10])
        r = self.make(line, clause, "rate x crew x hours beyond the call length", "crew log",
                      evidence_ids=[e["id"] for e in logs])
        if not logs:
            r.full, r.strict = r.billed, ZERO
            r = self.add(r)
            self.issue("missing_evidence", "hold", r.full, "no crew log for the overtime billed", r,
                       request={"to_role": "event_ops", "about": "crew_log"})
            return r
        crew_hours = ZERO
        parts = []
        for log in logs:
            value = jsonish(log["value"])
            hours = _hours_between(value["call_start"], value["call_end"])
            over = max(ZERO, hours - call)
            crew = dec(value.get("crew"), dec(params.get("crew_size"), ZERO))
            crew_hours += crew * over
            parts.append(f"{log['id']}: {hours} h call, {over} h over x {crew} crew")
        r.full = r.strict = min(n, crew_hours) * rate
        r.detail = f"billed {n} crew-hours at {fmt(unit)}; " + "; ".join(parts)
        r = self.add(r)
        self.issue("rate_above_contract", "dispute", n * max(ZERO, unit - rate),
                   f"billed {fmt(unit)} per crew-hour against {fmt(rate)}", r)
        self.issue("overtime_overbilled", "dispute", max(ZERO, n - crew_hours) * min(unit, rate),
                   f"billed {n} crew-hours of overtime; the crew log shows {crew_hours}", r)
        self.underbilled(r, crew_hours * rate, f"the crew log shows {crew_hours} crew-hours of overtime")
        return r

    def venue_overtime(self, line: dict, clause: dict) -> LineResult:
        """Venue overtime: rate x hours after the cut-off from the load-out log, in increments rounded up."""
        params = clause["params"]
        rate = dec(params.get("rate_per_hour"))
        increment = dec(params.get("increment_hours"), Decimal("0.5"))
        detail = jsonish(line.get("detail"))
        n, unit = self.qty_price(line)
        if detail.get("hours"):
            n = dec(detail["hours"])
            unit = dec(line.get("amount")) / n if n else unit
        logs = self.evidence("load_out_log", vendor=True)
        r = self.make(line, clause, f"rate x hours after {params.get('after')} in {increment} h increments "
                      "rounded up", "load-out log", evidence_ids=[e["id"] for e in logs])
        if len(logs) != 1:
            r.full, r.strict = r.billed, ZERO
            r = self.add(r)
            self.issue("missing_evidence", "hold", r.full, "no single load-out log for the overtime billed", r,
                       request={"to_role": "event_ops", "about": "load_out_log"})
            return r
        hours = venue_overtime_hours(jsonish(logs[0]["value"])["ended_at"], str(params.get("after", "23:00")),
                                     increment)
        r.full = r.strict = min(n, hours) * rate
        r.detail = f"billed {n} h at {fmt(unit)}; load-out ended {jsonish(logs[0]['value'])['ended_at']}: {hours} h"
        r = self.add(r)
        self.issue("rate_above_contract", "dispute", n * max(ZERO, unit - rate),
                   f"billed {fmt(unit)} per hour against {fmt(rate)}", r)
        self.issue("overtime_overbilled", "dispute", max(ZERO, n - hours) * min(unit, rate),
                   f"billed {n} hours of overtime; the load-out log gives {hours}", r)
        self.underbilled(r, hours * rate, f"the load-out log gives {hours} hours of overtime")
        return r

    def item_key(self, line: dict, items: dict) -> str | None:
        if line.get("item") in items:
            return str(line["item"])
        ref = str(line.get("clause_ref") or "")
        if "." in ref and ref.split(".", 1)[1] in items:
            return ref.split(".", 1)[1]
        words = _norm(line.get("description")) + _norm(line.get("item"))
        for key in items:
            stem = _norm(key)
            if stem in words or stem.rstrip("s") in words:
                return key
        if self.d.po:
            on_po = [str(p.get("clause_ref")).split(".", 1)[1] for p in self.d.po.get("lines", [])
                     if "." in str(p.get("clause_ref")) and str(p.get("clause_ref")).split(".", 1)[1] in items]
            if len(on_po) == 1:
                return on_po[0]
        return next(iter(items)) if len(items) == 1 else None

    def merchandise(self, line: dict, clause: dict) -> LineResult:
        """Goods: unit price x quantity delivered (signed delivery receipts), never more than ordered."""
        items = clause["params"].get("items") or {}
        key = self.item_key(line, items)
        n, unit = self.qty_price(line)
        if key is None:
            return self.change_order(line, None)
        ordered = dec(items[key].get("qty"))
        price = dec(items[key].get("unit_price"))
        receipts = [e for e in self.evidence("delivery_receipt", vendor=True)
                    if _norm(jsonish(e["value"]).get("item")) == _norm(key)]
        signed = [e for e in receipts if self.signed_by_ops(e)]
        delivered = sum((dec(jsonish(e["value"]).get("qty")) for e in signed), ZERO)
        basis = self.clause_of_type("delivery_basis")
        r = self.make(line, clause, "unit price x quantity delivered, never more than ordered",
                      "delivery receipt signed by event ops", evidence_ids=[e["id"] for e in signed])
        if basis:
            r.clause_ids.append(basis["clause_id"])
        billable_now = min(delivered, ordered, n)
        within_order = min(n, ordered)
        r.full = within_order * min(unit, price)
        r.strict = billable_now * min(unit, price)
        r.detail = f"billed {n} {key} at {fmt(unit)}; ordered {ordered} at {fmt(price)}; delivered {delivered}"
        r = self.add(r)
        self.issue("rate_above_contract", "dispute", n * max(ZERO, unit - price),
                   f"billed {fmt(unit)} per {key} against {fmt(price)}", r)
        self.issue("partial_delivery", "dispute", max(ZERO, n - ordered) * min(unit, price),
                   f"billed {n} {key} but only {ordered} were ordered", r)
        self.issue("partial_delivery", "hold", r.full - r.strict,
                   f"billed {n} {key}; signed delivery receipts show {delivered}" if signed else
                   f"no delivery receipt signed by event ops for {key}", r,
                   request={"to_role": "event_ops", "about": "delivery_receipt"})
        if delivered > n:
            self.issue("possible_underbilling", "info", (min(delivered, ordered) - n) * price,
                       f"{delivered} {key} were delivered but {n} invoiced", r)
        return r

    def lodging(self, line: dict, clause: dict) -> LineResult:
        """Room block: max(room-nights picked up, attrition minimum) x rate, never more than invoiced."""
        params = clause["params"]
        rate = dec(params.get("rate"))
        block = dec(params.get("room_nights"), ZERO)
        attrition = self.clause_of_type("attrition")
        minimum = ZERO
        if attrition:
            a = attrition["params"]
            if a.get("min_room_nights") not in (None, ""):
                minimum = dec(a["min_room_nights"])
            elif a.get("min_pct") not in (None, ""):
                minimum = (block * _pct(a["min_pct"])).to_integral_value(rounding=ROUND_CEILING)
        n, unit = self.qty_price(line)
        pickups = [e for e in self.evidence("room_pickup", vendor=False) if self.signed_by_ops(e)]
        clause_ids = [clause["clause_id"]] + ([attrition["clause_id"]] if attrition else [])
        r = self.make(line, clause, "max(room-nights picked up, attrition minimum) x rate",
                      "hotel pickup report confirmed by event ops", evidence_ids=[e["id"] for e in pickups])
        r.clause_ids = clause_ids
        if len(pickups) != 1:
            r.full = n * min(unit, rate)
            r.strict = min(minimum, n) * min(unit, rate)
            r = self.add(r)
            self.issue("missing_evidence", "hold", r.full - r.strict,
                       f"no pickup report confirmed by event ops; paying the attrition minimum of {minimum}", r,
                       request={"to_role": "event_ops", "about": "room_pickup"})
            return r
        picked = count_from(jsonish(pickups[0]["value"]), ("room_nights", "nights", "count")) or ZERO
        billable = min(max(picked, minimum), n)
        r.full = r.strict = billable * min(unit, rate)
        r.detail = f"billed {n} room-nights at {fmt(unit)}; picked up {picked}; minimum {minimum}; billable {billable}"
        r = self.add(r)
        self.issue("rate_above_contract", "dispute", n * max(ZERO, unit - rate),
                   f"billed {fmt(unit)} per room-night against {fmt(rate)}", r)
        self.issue("attrition_misapplied", "dispute", (n - billable) * min(unit, rate),
                   f"billed {n} room-nights; pickup was {picked} and the attrition minimum is {minimum}, so "
                   f"{billable} are billable", r)
        if picked > n:
            self.issue("possible_underbilling", "info", (picked - n) * rate,
                       f"{picked} room-nights were picked up but {n} invoiced", r)
        return r

    def cancellation(self, line: dict, clause: dict) -> LineResult:
        """Cancellation fee by tier: calendar days from the notice to the event start; min_days is inclusive."""
        params = clause["params"]
        notices = self.evidence("cancellation_notice")
        r = self.make(line, clause, "tier by calendar days from notice to event start (min_days inclusive)",
                      "cancellation notice and contract clause", evidence_ids=[e["id"] for e in notices])
        start = (self.d.event or {}).get("start_date")
        if len(notices) != 1 or not start:
            r.full, r.strict = r.billed, ZERO
            r = self.add(r)
            self.issue("missing_evidence", "hold", r.full, "no single cancellation notice to date the tier", r,
                       request={"to_role": "event_ops", "about": "cancellation_notice"})
            return r
        value = jsonish(notices[0]["value"])
        notice_day = str(value.get("notice_date") or value.get("date") or notices[0]["date"])[:10]
        days = cancellation_days(notice_day, str(start)[:10])
        pct = cancellation_pct(params.get("tiers") or [], days)
        base = self.cancellation_base(params)
        expected = cents(base * pct)
        r.full = r.strict = expected
        r.detail = f"notice {notice_day}, event {str(start)[:10]}: {days} days; tier {pct}; base {fmt(base)}"
        r = self.add(r)
        self.issue("cancellation_tier_wrong", "dispute", r.billed - r.full,
                   f"notice given {days} days before the event puts the fee at {pct} of {fmt(base)} = "
                   f"{fmt(expected)}", r)
        self.underbilled(r, expected, f"the tier for {days} days gives {fmt(expected)}")
        return r

    def cancellation_base(self, params: dict) -> Decimal:
        for key in ("base_amount", "amount"):
            if params.get(key) not in (None, ""):
                return dec(params[key])
        base = params.get("base") or params.get("of")
        if base in self.d.clauses:
            p = self.d.clauses[base]["params"]
            return dec(p.get("fixed", p.get("net", p.get("amount"))))
        total = ZERO
        for c in self.d.clauses.values():
            if c["type"] in ("venue_hire", "fixed_fee", "fixed_price", "av_package", "meeting_space"):
                total += dec(c["params"].get("fixed", c["params"].get("net", c["params"].get("amount"))))
        return total

    def change_order(self, line: dict, clause: dict | None) -> LineResult:
        """Work outside the signed terms: billable only with a change order signed by the event owner."""
        co_clause = clause if (clause or {}).get("type") == "change_order" else self.clause_of_type("change_order")
        signed = [e for e in self.evidence("change_order", vendor=True)
                  if e.get("signed_by") and e["signed_by"] == self.d.owner]
        words = _norm(line.get("description"))
        match = [e for e in signed if _norm(jsonish(e["value"]).get("description") or
                                            jsonish(e["value"]).get("item")) in words
                 or dec(jsonish(e["value"]).get("amount"), ZERO) == dec(line.get("amount"))]
        r = self.make(line, co_clause, "billable only with a change order signed by the event owner",
                      "change order signed by the event owner", evidence_ids=[e["id"] for e in match[:1]])
        if match:
            agreed = dec(jsonish(match[0]["value"]).get("amount"), r.billed)
            r.full = r.strict = agreed
            r = self.add(r)
            self.issue("rate_above_contract", "dispute", r.billed - r.full,
                       f"billed {fmt(r.billed)} against the signed change order for {fmt(agreed)}", r)
            return r
        r.full, r.strict = r.billed, ZERO
        r.detail = "no change order signed by the event owner" if co_clause else "no clause covers this line"
        r = self.add(r)
        self.issue("unapproved_change_order", "hold", r.full,
                   f"{r.description or 'line'} is not covered by a change order signed by the event owner", r,
                   request={"to_role": "event_owner", "about": "signed_change_order"})
        return r

    # -- service charge, tax and VAT ------------------------------------------------------------
    def food_lines(self) -> list[LineResult]:
        return [r for r in self.base if getattr(r, "food", False)]

    def service_line(self, line: dict, clause: dict | None) -> LineResult:
        clause = clause if (clause or {}).get("type") == "service_charge" else self.clause_of_type("service_charge")
        r = self.make(line, clause, "service charge % x food", "contract clause")
        food = self.food_lines()
        r.account = food[0].account if food else r.account
        if clause is None:
            r.full = r.strict = ZERO
            r = self.add(r)
            self.issue("rate_above_contract", "dispute", r.billed, "the contract has no service charge", r)
            return r
        pct = _pct(clause["params"].get("service_charge_pct"))
        r.full = pct * sum((f.full for f in food), ZERO)
        r.strict = pct * sum((f.strict for f in food), ZERO)
        r.clause_ids += [c for f in food for c in f.clause_ids if c not in r.clause_ids]
        r.evidence_ids = sorted({e for f in food for e in f.evidence_ids})
        r.detail = f"{pct} x food"
        r = self.add(r)
        self.knock_on(r, pct * sum((f.billed for f in food), ZERO), f"service charge billed above {pct} of food")
        return r

    def knock_on(self, r: LineResult, at_billed_base: Decimal, rate_reason: str) -> None:
        """A percentage line follows its base: what it bills above the rate on the billed base is a rate
        issue; the rest of its difference follows the base lines' own issues."""
        over_rate = max(ZERO, r.billed - cents(at_billed_base))
        if over_rate > CENT / 2:
            self.issue("rate_above_contract", "dispute", over_rate, rate_reason, r)
        rest = r.billed - r.full - over_rate
        base_disputes = [i for i in self.issues if i.kind == "dispute" and i.flag != "rate_above_contract"
                         and i.line_id in {b.line_id for b in self.base}]
        flag = max(base_disputes, key=lambda i: i.amount).flag if base_disputes else "rate_above_contract"
        if rest > 0:
            self.issue(flag, "dispute", rest, f"follows the base it is charged on ({flag})", r)
        held = r.full - r.strict
        base_holds = [i for i in self.issues if i.kind == "hold" and i.line_id in {b.line_id for b in self.base}]
        if held > 0:
            hold = max(base_holds, key=lambda i: i.amount) if base_holds else None
            self.issue(hold.flag if hold else "missing_evidence", "hold", held,
                       "follows the held part of the base it is charged on", r,
                       request=hold.request if hold else None)

    def tax_line(self, line: dict, clause: dict | None) -> LineResult:
        if clause and clause["type"] == "deposit":
            return self.deposit_vat(line, clause)
        tax_clause = clause if (clause or {}).get("type") in ("sales_tax", "vat") else None
        own = clause if clause and clause["params"].get("vat_pct") not in (None, "") and \
            clause["type"] not in ("sales_tax", "vat") else None
        if tax_clause is None and own is None:
            want = "vat" if line.get("kind") == "vat" else "sales_tax"
            tax_clause = self.clause_of_type(want) or self.clause_of_type("sales_tax", "vat")
        if tax_clause is None and own is None:
            hired = [b for b in self.base if getattr(b, "vat_pct", None)]
            if hired:
                own = self.d.clauses[hired[0].clause_ids[0]]
        r = self.make(line, tax_clause or own, "tax rate x taxable base per the clause", "contract clause")
        if tax_clause is None and own is None:
            r.full = r.strict = ZERO
            r = self.add(r)
            self.issue("rate_above_contract", "dispute", r.billed, "the contract sets no tax or VAT on this invoice", r)
            return r
        if own is not None:
            base = [b for b in self.base if own["clause_id"] in b.clause_ids]
            rate = _pct(own["params"]["vat_pct"])
            r.rule = "VAT % x the clause amount"
        else:
            params = tax_clause["params"]
            rate = _pct(self.d.policies.get(params["rate_ref"])) if params.get("rate_ref") else \
                _pct(params.get("vat_pct", params.get("tax_pct", params.get("rate"))))
            if params.get("rate_ref"):
                r.clause_ids.append(str(params["rate_ref"]))
            taxable = [b for b in self.base if not getattr(b, "inclusive", False)]
            food = self.food_lines()
            if params.get("base") == "food":
                base = food
            elif params.get("base") == "food_plus_service":
                base = food + self.svc
            else:
                base = taxable + self.svc
            r.clause_ids += [c for b in base for c in b.clause_ids if c not in r.clause_ids]
        r.account = base[0].account if base else r.account
        r.evidence_ids = sorted({e for b in base for e in b.evidence_ids})
        r.full = rate * sum((b.full for b in base), ZERO)
        r.strict = rate * sum((b.strict for b in base), ZERO)
        r.detail = f"{rate} x {fmt(cents(sum((b.full for b in base), ZERO)))}"
        r.is_vat = line.get("kind") == "vat" or (tax_clause or {}).get("type") == "vat" or own is not None
        r = self.add(r)
        self.knock_on(r, rate * sum((b.billed for b in base), ZERO), f"tax billed above {rate} of its base")
        return r

    def deposit_vat(self, line: dict, clause: dict) -> LineResult:
        expected = dec(clause["params"].get("vat"), ZERO)
        r = self.make(line, clause, "VAT on the deposit per the contract", "contract clause")
        r.full = r.strict = expected
        r.is_vat = True
        r = self.add(r)
        self.issue("rate_above_contract", "dispute", r.billed - r.full,
                   f"VAT on the deposit billed {fmt(r.billed)} against {fmt(expected)}", r)
        return r

    # -- deposit credits --------------------------------------------------------------------------
    def deposit_applies(self, deposit: dict) -> bool:
        clause = self.d.clauses.get(deposit["clause_ref"])
        if clause is None or deposit["clause_ref"] in self.d.credited:
            return False
        credited = clause["params"].get("credited_on_final", True)
        if credited is False or str(credited).lower() == "false":
            return False
        # A deposit secures the main service: it is credited on the invoice for that service (the clause it is
        # applied to, else the contract's main priced clause on the deposit's PO), not on an extra such as overtime.
        types = {(self.d.clauses.get(r.clause_ids[0]) or {}).get("type") for r in self.base if r.clause_ids}
        applied = APPLIED_TO.get(str(clause["params"].get("applied_to", "")).lower())
        if applied:
            return applied in types
        if not types & MAIN:
            return False
        po_refs = {str(p.get("clause_ref")) for p in (self.d.po or {}).get("lines", [])}
        return deposit["clause_ref"] in po_refs if po_refs else True

    def credits(self, credit_lines: list[tuple[dict, dict | None]]) -> None:
        unmatched = list(credit_lines)
        for deposit in self.d.deposits:
            if not self.deposit_applies(deposit):
                continue
            clause = self.d.clauses[deposit["clause_ref"]]
            how = str(clause["params"].get("credited_on_final", "gross")).lower()
            credit = dec(deposit["net"]) if how == "net" else dec(deposit["gross"])
            line = next((l for l, c in unmatched if (c or {}).get("clause_id") == clause["clause_id"]), None)
            if line is None and len(unmatched) == 1:
                line = unmatched[0][0]
            if line is not None:
                unmatched = [(l, c) for l, c in unmatched if l is not line]
            r = self.make(line, clause, "deposit credited against the final invoice as paid", "deposit payment",
                          billed=dec(line.get("amount")) if line else ZERO, evidence_ids=[deposit["invoice"]])
            r.kind = "deposit_credit"
            r.description = r.description or f"Deposit {deposit['invoice']} (not credited on the invoice)"
            r.full = r.strict = -credit
            r.detail = f"deposit {deposit['invoice']} paid {fmt(dec(deposit['gross']))}; credited {how}"
            r = self.add(r)
            short = r.billed - r.full                 # how much of the credit is missing
            if short <= 0:
                self.underbilled_credit(r, short)
                continue
            if line is None:
                self.issue("deposit_not_applied", "dispute", short,
                           f"the deposit of {fmt(credit)} paid on {deposit['invoice']} is not credited", r)
            elif dec(deposit.get("vat"), ZERO) > 0 and short == dec(deposit["vat"]):
                self.issue("vat_on_deposit_double_charged", "dispute", short,
                           f"the deposit is credited net ({fmt(-r.billed)}) though its VAT of {fmt(short)} was paid "
                           f"too; the contract credits it gross ({fmt(credit)})", r)
            else:
                self.issue("deposit_not_applied", "dispute", short,
                           f"the deposit credit is {fmt(-r.billed)} but {fmt(credit)} was paid", r)
        for line, clause in unmatched:
            r = self.make(line, clause, "a credit no paid deposit explains", "deposit payment")
            r.full = r.strict = ZERO
            r = self.add(r)
            self.underbilled_credit(r, r.billed)

    def underbilled_credit(self, r: LineResult, short: Decimal) -> None:
        if short < 0:
            self.issue("possible_underbilling", "info", -short,
                       "the invoice credits more than the deposit paid; it is paid as billed", r)


def cancellation_days(notice: str, start: str) -> int:
    return (date.fromisoformat(start) - date.fromisoformat(notice)).days


def cancellation_pct(tiers: list, days: int) -> Decimal:
    """The tier with the highest min_days that the notice reaches (min_days inclusive)."""
    reached = [t for t in tiers if days >= int(t.get("min_days", 0))]
    if not reached:
        return Decimal(1)
    tier = max(reached, key=lambda t: int(t.get("min_days", 0)))
    return _pct(tier.get("pct", tier.get("percent", tier.get("fee_pct", 1))))


def match(data: MatchInput) -> MatchResult:
    return Matcher(data).run()
