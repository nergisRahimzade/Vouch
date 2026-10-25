"""Deterministic POL-MAT routing; payroll is a recipient, never an approver."""
from decimal import Decimal

INFO_FLAGS = {"third_event_allowance_reassigned"}


def route(*, flags=(), wage_taxable=Decimal(0), nondeductible=Decimal(0),
          flat_tax_37b=Decimal(0), corrections=(), escalations=(), blocked=False):
    correction_total = sum((abs(Decimal(str(c["amount"]))) for c in corrections
                            if c["type"] in {"reclass", "vat_reversal"}), Decimal(0))
    material = (set(flags) - INFO_FLAGS or Decimal(str(wage_taxable)) != 0 or corrections
                or Decimal(str(nondeductible)) >= 1000 or Decimal(str(flat_tax_37b)) >= 1000)
    approvers = [] if blocked else ["tax_manager" if material else "policy"]
    if correction_total >= 5000 and not blocked:
        approvers.append("controller")
    return {"approvers": approvers, "escalations": sorted(set(escalations)),
            "policy": "POL-MAT"}


def delegate(conn, person_id, deadline):
    """Use an explicitly recorded delegation only when an absence extends past the deadline."""
    import json
    row = conn.execute("SELECT extra FROM people WHERE id = ?", (person_id,)).fetchone()
    extra = json.loads(row[0] or "{}") if row else {}
    absent_until = extra.get("out_of_office_until") or extra.get("ooo_until")
    if not absent_until or str(absent_until) < str(deadline):
        return person_id
    day = conn.execute("SELECT value FROM scenario WHERE key = 'as_of'").fetchone()[0]
    delegated = conn.execute("SELECT to_person FROM delegations WHERE from_person = ? "
                             "AND valid_from <= ? AND valid_to >= ? ORDER BY id",
                             (person_id, day, str(deadline)[:10])).fetchone()
    return delegated[0] if delegated else person_id
