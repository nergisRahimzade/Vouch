"""Row-level diff between two in-memory world bundles (test helper)."""
from pathlib import PurePosixPath

ROW_KEY = {
    "hr_census": lambda r: r["employee_id"],
    "payroll_register": lambda r: r["employee_id"],
    "payment_file": lambda r: r["payment_id"],
    "bank_statement": lambda r: r["reference"],
    "equity_report": lambda r: r["grant_id"],
    "ledger_booked": lambda r: (r["entry_id"], r["account"], r["department"], r["source_ref"]),
    "salary_changes": lambda r: r["change_id"],
    "approvals_log": lambda r: r["request_id"],
}


def _kind(path):
    stem = PurePosixPath(path).stem
    return next(k for k in ROW_KEY if stem.startswith(k))


def diff(before: dict, after: dict) -> dict:
    """path -> {'added': [...], 'removed': [...], 'changed': {key: {field: (old, new)}}} or 'json'."""
    out = {}
    for path in sorted(set(before) | set(after)):
        a, b = before.get(path), after.get(path)
        if a == b:
            continue
        if not path.endswith(".csv"):
            out[path] = "json"
            continue
        key = ROW_KEY[_kind(path)]
        old, new = {key(r): r for r in a or []}, {key(r): r for r in b or []}
        out[path] = {
            "added": [new[k] for k in new if k not in old],
            "removed": [old[k] for k in old if k not in new],
            "changed": {k: {f: (old[k][f], new[k][f]) for f in new[k] if old[k][f] != new[k][f]}
                        for k in new if k in old and old[k] != new[k]},
        }
    return out
