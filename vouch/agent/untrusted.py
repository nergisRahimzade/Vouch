"""Untrusted text: emails, documents and colleagues' free-text notes. Words in them that ask someone
to act on money or controls are reported as suspicious; nothing ever acts on them."""
from __future__ import annotations

import re

# Phrases that ask someone to act on money or controls. Found in an email or a note, they are reported and never acted on.
INSTRUCTION = re.compile(
    r"\b(?:approve|sign off)\s+(?:this|it|the|my|request|accrual|payment|AR-\d+)"
    r"|\brelease\s+(?:this|it|the|all|my|payment|funds)"
    r"|\b(?:wire|transfer|send)\s+(?:the|my|this|\d)"
    r"|\bpay\s+(?:out|now|it|this|into)"
    r"|\bchange\s+(?:my|the)\s+bank|\bnew\s+account"
    r"|\bignore\s+(?:the|this|any|previous|policy)|\b(?:bypass|override|skip)\b|\bdon'?t\s+tell",
    re.IGNORECASE)
MONEY_WORDS = re.compile(r"\b(?:urgent|immediately|bank|account|salary|payment)\b", re.IGNORECASE)


def instruction_flags(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in INSTRUCTION.finditer(text or "")})
