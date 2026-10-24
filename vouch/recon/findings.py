"""A finding: one real difference between sources for the same fact. Every finding names the
sources it compared and what each said, the source it trusts and why, the rule, and the amount."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

from vouch.money import fmt
from vouch.recon.sources import Row

# The system of record for each fact (spec, Layer 4) and why it is trusted.
TRUST = {
    "employment": ("hr_census", "HR is the system of record for who works here, their dates, entity and department"),
    "bank_details": ("hr_census", "HR is the system of record for bank details; changes need HR and a call-back"),
    "salary": ("salary_changes", "approved salary changes are the system of record for salary"),
    "grant": ("signed_grants and board_approvals", "the signed grant and the board approval are the record for "
                                                   "grant terms and approval"),
    "cash": ("bank_statement", "the bank is the record of cash actually moved"),
    "rates": ("pay_policy", "the company pay policy is the record for contribution rates"),
    "statutory": ("pay_function", "statutory rates from the official sources, computed by the one pay function"),
    "one_payment": ("payroll_register", "the register sets one net payment per person, entity and month"),
    "duplicate_payment": ("payment_file", "the first line for a payment in the payment file is the payment; a later "
                                          "line repeating it is rejected (FIN-08)"),
    "row_layout": ("file_layout", "a row must carry every field its file defines before anything is computed or "
                                  "paid from it"),
    "repeated_row": ("the first of the repeated rows", "a row that repeats an earlier one exactly is the same item; "
                                                       "it is counted once and the repeat is held (FIN-08)"),
    "approval": ("finance_policy", "the finance and controls policy decides who may approve what"),
    "fx": ("exchange_rate_policy", "each month is translated at its recorded monthly average rate"),
    "ledger": ("journal", "every booked line must rest on evidence that agrees with it"),
}


@dataclass(frozen=True)
class Evidence:
    """One side of a comparison: which source, which rows, and what it says."""
    source: str
    path: str
    rows: tuple[int, ...]
    says: str

    @staticmethod
    def of(row: Row, says: str) -> "Evidence":
        return Evidence(row.kind, row.path, (row.row_no,), says)

    def to_dict(self) -> dict:
        return {"source": self.source, "path": self.path, "rows": list(self.rows), "says": self.says}


@dataclass
class Finding:
    check: str
    kind: str
    period: str                         # the month the fact belongs to
    entity: str | None
    fact: str                           # key into TRUST
    subject: dict                       # identifiers of what the finding is about
    summary: str
    evidence: list[Evidence]
    rules: list[str]
    amount: Decimal | None
    currency: str | None
    amount_basis: str
    proposed_action: str
    ask_role: str | None
    ask_person: str | None = None
    disposition: str = "case"           # case | explained
    explanation: str = ""
    precedents: list[dict] = field(default_factory=list)
    merge_key: str | None = None        # findings with the same kind, period and key are one finding

    @property
    def trusted_source(self) -> str:
        return TRUST[self.fact][0]

    @property
    def trust_reason(self) -> str:
        return TRUST[self.fact][1]

    @property
    def finding_id(self) -> str:
        key = self.merge_key or json.dumps(self.subject, sort_keys=True, default=str)
        return "F-" + hashlib.sha1(f"{self.kind}|{self.period}|{key}".encode()).hexdigest()[:12]

    def to_record(self) -> dict:
        return {
            "finding_id": self.finding_id, "check": self.check, "kind": self.kind, "period": self.period,
            "entity": self.entity, "disposition": self.disposition, "subject": self.subject,
            "summary": self.summary, "evidence": [e.to_dict() for e in self.evidence],
            "trusted_source": self.trusted_source, "trust_reason": self.trust_reason, "rules": self.rules,
            "precedents": self.precedents, "amount": fmt(self.amount) if self.amount is not None else None,
            "currency": self.currency, "amount_basis": self.amount_basis, "proposed_action": self.proposed_action,
            "ask_role": self.ask_role, "ask_person": self.ask_person, "explanation": self.explanation,
        }


class IncompleteFinding(ValueError):
    """A finding that cannot explain itself is never stored."""


def problems(finding: "Finding", known_rules: set[str] | None = None) -> list[str]:
    """What a finding lacks: the sources compared, the trusted source and why, the rule, the amount."""
    out = []
    if not finding.summary.strip():
        out.append("no summary")
    if len(finding.evidence) < 2:
        out.append("fewer than two sources compared")
    if any(not (e.source and e.path and e.says) for e in finding.evidence):
        out.append("a compared source does not say what it shows")
    if finding.fact not in TRUST:
        out.append(f"no trusted source for fact {finding.fact!r}")
    if not finding.rules:
        out.append("no rule")
    elif known_rules is not None and set(finding.rules) - known_rules:
        out.append(f"unknown rules {sorted(set(finding.rules) - known_rules)}")
    if finding.amount is None or not finding.currency or not finding.amount_basis.strip():
        out.append("no amount, currency or amount basis")
    if finding.disposition == "explained" and not finding.explanation.strip():
        out.append("explained without an explanation")
    return out


class Findings:
    """Collects findings; a finding with the same id as an earlier one adds its evidence to it."""

    def __init__(self):
        self.items: dict[str, Finding] = {}

    def validate(self, known_rules: set[str]) -> None:
        """Refuse the whole set if any finding cannot explain itself."""
        for finding in self:
            missing = problems(finding, known_rules)
            if missing:
                raise IncompleteFinding(f"{finding.kind} {finding.period} {finding.subject}: {'; '.join(missing)}")

    def add(self, finding: Finding) -> Finding:
        missing = problems(finding)
        if missing:
            raise IncompleteFinding(f"{finding.kind} {finding.period} {finding.subject}: {'; '.join(missing)}")
        existing = self.items.get(finding.finding_id)
        if existing is None:
            self.items[finding.finding_id] = finding
            return finding
        for evidence in finding.evidence:
            if evidence not in existing.evidence:
                existing.evidence.append(evidence)
        for rule in finding.rules:
            if rule not in existing.rules:
                existing.rules.append(rule)
        existing.subject = {**finding.subject, **existing.subject}
        return existing

    def has(self, kind: str, period: str, key: str) -> bool:
        return any(f.kind == kind and f.period == period and f.merge_key == key for f in self.items.values())

    def __iter__(self):
        return iter(sorted(self.items.values(), key=lambda f: (f.period, f.kind, f.finding_id)))

    def cases(self) -> list[Finding]:
        return [f for f in self if f.disposition == "case"]

    def explained(self) -> list[Finding]:
        return [f for f in self if f.disposition == "explained"]
