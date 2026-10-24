"""Everything reconciliation reads, taken from the database as loaded. Nothing here
knows about the generator, the answer key or any particular person, grant or payment."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property

from vouch import dates
from vouch.db.rows import MONEY_FILES, OPTIONAL, row_problems  # noqa: F401 (re-exported)


@dataclass(frozen=True)
class Row:
    doc_id: int
    row_no: int
    path: str
    kind: str
    month: str | None
    entity: str | None
    data: dict

    def __getitem__(self, key: str) -> str:
        return self.data[key]

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def amount(self, key: str) -> Decimal:
        return Decimal(self.data[key])


class Sources:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.by_kind: dict[str, list[Row]] = defaultdict(list)
        self.content: dict[str, object] = {}
        query = """SELECT d.doc_id, d.path, d.kind, d.month, d.entity, r.row_no, r.data
                   FROM documents d JOIN source_rows r USING (doc_id) ORDER BY d.path, r.row_no"""
        for r in conn.execute(query):
            self.by_kind[r["kind"]].append(Row(r["doc_id"], r["row_no"], r["path"], r["kind"], r["month"],
                                               r["entity"], json.loads(r["data"])))
        for r in conn.execute("SELECT kind, content FROM documents WHERE month IS NULL AND path LIKE '%.json'"):
            self.content[r["kind"]] = json.loads(r["content"])
        # Rows set aside: incomplete ones, and exact repeats in money files. rows() never returns them;
        # reconciliation turns each into a case.
        self.incomplete: list[tuple[Row, list[str]]] = []
        self.repeats: list[tuple[Row, Row]] = []                      # (repeat, the earlier row it repeats)
        for kind in OPTIONAL:
            first: dict[tuple, Row] = {}
            for row in self.by_kind.get(kind, []):
                problems = row_problems(kind, row.data)
                if problems:
                    self.incomplete.append((row, problems))
                    continue
                key = (row.doc_id, json.dumps(row.data, sort_keys=True))
                if kind in MONEY_FILES and key in first:
                    self.repeats.append((row, first[key]))
                first.setdefault(key, row)
        self.set_aside = {(r.doc_id, r.row_no) for r, _ in self.incomplete + self.repeats}

    def set_aside_people(self, kind: str, month: str | None) -> set[str]:
        """Employees with a row of this kind set aside in this month."""
        return {r.get("employee_id") for r, _ in self.incomplete + self.repeats if r.kind == kind and r.month == month}

    # -- company and policies ---------------------------------------------------
    @cached_property
    def company(self) -> dict:
        return self.content["company"]

    @property
    def open_month(self) -> str:
        return self.company["open_month"]

    @cached_property
    def months(self) -> list[str]:
        return sorted({r.month for rows in self.by_kind.values() for r in rows if r.month})

    def currency(self, entity: str) -> str:
        return self.company["functional_currency_by_entity"][entity]

    def rule(self, rule_id: str) -> dict:
        for document in ("pay_policy", "finance_policy"):
            for rule in self.content[document]["rules"]:
                if rule["rule_id"] == rule_id:
                    return rule
        raise KeyError(rule_id)

    @cached_property
    def rule_ids(self) -> set[str]:
        return {r["rule_id"] for d in ("pay_policy", "finance_policy") for r in self.content[d]["rules"]}

    def materiality(self, currency: str) -> Decimal:
        return Decimal(self.rule("FIN-01")["threshold"][currency])

    def large_entry_limit(self, currency: str) -> Decimal:
        return Decimal(self.rule("FIN-04")["limit"][currency])

    def pension_rate(self, entity: str) -> Decimal | None:
        value = self.rule("PAY-04").get("employer_pension_rate", {}).get(entity)
        return Decimal(value) if value is not None else None

    def person_with_role(self, role: str, department: str | None = None) -> str:
        for person in self.content["directory"]["people"]:
            if person["role"] == role and (department is None or person.get("department") == department):
                return person["name"]
        raise KeyError(role)

    # -- monthly sources --------------------------------------------------------
    def rows(self, kind: str, month: str | None = None, entity: str | None = None) -> list[Row]:
        return [r for r in self.by_kind.get(kind, [])
                if (month is None or r.month == month) and (entity is None or r.entity == entity)
                and (r.doc_id, r.row_no) not in self.set_aside]

    @cached_property
    def entities(self) -> list[str]:
        return [e["entity"] for e in self.company["entities"]]

    def is_closed(self, month: str) -> bool:
        return month < self.open_month

    @staticmethod
    def month_end(month: str):
        return dates.month_end(month)
