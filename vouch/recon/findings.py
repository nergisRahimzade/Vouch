"""Event-tax findings carry their evidence and dated rule citations."""
from dataclasses import dataclass, field, asdict
import hashlib
import json


@dataclass
class Finding:
    kind: str
    summary: str
    evidence: list
    rules: list
    request: dict | None = None
    severity: str = "needs_person"
    amount: str | None = None
    policies: list = field(default_factory=list)

    @property
    def finding_id(self):
        raw = json.dumps([self.kind, self.evidence], sort_keys=True, default=str)
        return "F-" + hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_record(self):
        return {"finding_id": self.finding_id, **asdict(self)}


class Findings:
    def __init__(self):
        self.items = {}

    def add(self, finding):
        if not finding.evidence or not finding.rules or not finding.summary:
            raise ValueError("a finding requires evidence, a rule and an explanation")
        self.items[finding.finding_id] = finding
        return finding

    def __iter__(self):
        return iter(self.items.values())

    def cases(self):
        return [f for f in self if f.severity != "ok"]

    def explained(self):
        return [f for f in self if f.severity == "ok"]
