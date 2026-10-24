"""Whether a monthly source row can be used at all. A row that fails is never computed or paid
from: the subledgers leave it out and reconciliation turns it into a case."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

# What a monthly row must carry before anything is computed or paid from it. Every field must be
# filled unless listed as optional, and every field not listed as text must be a number.
OPTIONAL = {"hr_census": {"exit_date"}, "payroll_register": set(), "payment_file": set(), "bank_statement": set(),
            "equity_report": {"grant_date", "fair_value_per_share_usd", "board_resolution"}}
TEXT = {"employee_id", "employee_name", "first_name", "last_name", "email", "entity", "department", "currency",
        "period", "pay_date", "status", "bank_account", "payment_id", "batch_id", "payee_name", "value_date",
        "bank_line_id", "account_name", "description", "reference", "date", "grant_id", "local_currency",
        "fx_rate_basis", "hire_date", "exit_date", "entity_since", "grant_date", "vest_start", "board_resolution"}
# Files that move money: an exact repeat of an earlier row is set aside, never counted twice.
MONEY_FILES = ("payroll_register", "payment_file", "bank_statement")


def row_problems(kind: str, data: dict) -> list[str]:
    """What a row lacks before it can be used: blank fields and fields that should be numbers."""
    out = []
    for field, value in data.items():
        if value in ("", None):
            if field not in OPTIONAL.get(kind, set()):
                out.append(f"{field} is blank")
        elif field not in TEXT:
            try:
                Decimal(value)
            except InvalidOperation:
                out.append(f"{field} is not a number ({value!r})")
    return out
