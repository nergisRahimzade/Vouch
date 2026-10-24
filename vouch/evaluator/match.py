"""Matches findings a run left in the database to the sealed answer key. Evaluator-only."""
from __future__ import annotations


def identifiers(subject: dict) -> set[str]:
    out = set()
    for key, value in subject.items():
        if key.endswith("_id") and isinstance(value, str) and value:
            out.add(value)
        elif key.endswith("_ids") and isinstance(value, list):
            out.update(v for v in value if isinstance(v, str))
        elif key == "unpaid_lines":
            out.update(line["payment_id"] for line in value)
    return out


def matches(item: dict, finding: dict) -> bool:
    """A finding is about a key item if they share an identifier; entity-wide items match on entity and month."""
    ids = identifiers(item["subject"])
    if ids:
        return bool(ids & identifiers(finding["subject"]))
    if finding.get("entity") != item["subject"].get("entity") or finding["period"] != item["month"]:
        return False
    file = item["subject"].get("file")
    return file is None or any(e["path"] == file for e in finding["evidence"])


def match(key_items: list[dict], findings: list[dict], disposition: str = "case") -> dict[str, list[dict]]:
    chosen = [f for f in findings if f["disposition"] == disposition]
    return {item.get("plant_id") or item.get("noise_id"): [f for f in chosen if matches(item, f)] for item in key_items}
