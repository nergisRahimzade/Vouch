"""Claim check: every number, date, rule and name in what Vouch writes must match a tool result
from the same run; otherwise the text is rejected."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

AMOUNT = re.compile(r"(?<![\w.-])-?\d{1,3}(?:,\d{3})+(?:\.\d{2})?(?![\w-])|(?<![\w.-])-?\d+\.\d{2}(?![\w-])")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
MONTH = re.compile(r"\b\d{4}-(?:0[1-9]|1[0-2])\b(?!-\d)")
RULE = re.compile(r"\b(?:PAY|FIN)-\d{2}\b")
IDENTIFIER = re.compile(r"\b(?:NL-\d{3,}|G-\d{3,}|PAY-\d{4}-\d{2}-[A-Z]{2}-\d+|BNK-[A-Z]{2}-\d{4}-\d{2}-\d+|EM-\d+|"
                        r"SC-\d+|AR-\d+|RUL-[\w-]+|REQ-\d+|Q-\d+|F-[0-9a-f]{12}|VCH-[\w-]+|BATCH-[\w-]+)\b")


@dataclass
class ClaimCheck:
    ok: bool
    problems: list[str] = field(default_factory=list)
    checked: int = 0


def _amounts(text: str) -> set[Decimal]:
    out = set()
    for match in AMOUNT.findall(text):
        try:
            value = Decimal(match.replace(",", ""))
        except InvalidOperation:
            continue
        out.update({value, abs(value)})
    return out


def check(text: str, evidence: list[str], names: list[str]) -> ClaimCheck:
    """`evidence` is every tool result of the run (as text); `names` are the people Vouch may name."""
    corpus = "\n".join(evidence)
    known_amounts = _amounts(corpus)
    problems, checked = [], 0
    for raw in AMOUNT.findall(text):
        checked += 1
        try:
            value = abs(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            continue
        if value not in known_amounts:
            problems.append(f"amount {raw} does not appear in any tool result")
    for pattern, label in ((DATE, "date"), (MONTH, "month"), (RULE, "rule"), (IDENTIFIER, "identifier")):
        for claim in set(pattern.findall(text)):
            checked += 1
            if claim not in corpus:
                problems.append(f"{label} {claim} does not appear in any tool result")
    for name in names:
        if name in text:
            checked += 1
            if name not in corpus:
                problems.append(f"name {name} does not appear in any tool result")
    return ClaimCheck(not problems, problems, checked)
