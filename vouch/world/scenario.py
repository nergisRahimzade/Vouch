"""The company world as it stands on one date.

Reads the base fixtures (Appendix A) and, for a scenario, adds its `setup` rows and applies its
`overrides` (spec §8.1). Then time travel (D19): every row dated after `as_of` is left out, so
nothing downstream can see the future and every status is derived from what is visible.

A scenario's `expect` block is the answer key. It is never part of a Scenario: `parse` drops it,
and only the evaluator reads it (vouch.evaluator.answer_key)."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from vouch import dates
from vouch.world.clauses import clause_type

FIXTURES = Path(__file__).with_name("fixtures.yaml")

# Tables a scenario may add rows to or override (spec §4.1), in load order.
TABLES = ["entities", "cost_centers", "people", "delegations", "events", "vendors", "vendor_bank_accounts",
          "vendor_master_changes", "contracts", "contract_clauses", "purchase_orders", "po_lines", "invoices",
          "invoice_lines", "evidence", "emails", "payments", "policies", "period_rates", "vendor_history",
          "journal_entries", "journal_lines"]
# Short field names the spec uses, mapped to column names.
ALIASES = {"events": {"start": "start_date", "end": "end_date"},
           "delegations": {"from": "from_person", "to": "to_person"},
           "vendor_bank_accounts": {"last4": "account_last4"},
           "emails": {"from": "from_address", "to": "to_address"},
           "journal_entries": {"id": "entry_id", "date": "entry_date", "memo": "reason", "invoice": "source_ref"},
           "invoices": {"po_id": "po", "number": "invoice_number"},
           "contract_clauses": {"id": "clause_id"},
           "evidence": {"signed": "signed_by"}}
# The date that decides whether a row is visible on as_of (time travel). Rows without one are always visible.
VISIBLE_BY = {"invoices": ("received_at", "invoice_date"), "payments": ("paid_at",), "emails": ("received_at",),
              "evidence": ("date",), "journal_entries": ("entry_date",), "vendor_master_changes": ("requested_at",),
              "vendor_history": ("date",), "contracts": ("signed_date",), "vendor_bank_accounts": ("active_from",),
              "purchase_orders": ("created_at",)}


class ScenarioError(ValueError):
    """A scenario that cannot be loaded as written; said plainly, never guessed around."""


class _Loader(yaml.SafeLoader):
    """YAML without floats or date objects: amounts become Decimal, dates stay ISO text."""


_Loader.add_constructor("tag:yaml.org,2002:float", lambda loader, node: Decimal(loader.construct_scalar(node)))
_Loader.add_constructor("tag:yaml.org,2002:timestamp", lambda loader, node: loader.construct_scalar(node))


def read_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


def read_yaml_text(text: str):
    return yaml.load(text, Loader=_Loader)


@dataclass
class Scenario:
    """What the company and Vouch may know about one scenario. Never carries `expect`."""
    id: str
    title: str
    as_of: str
    setup: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)
    people_responses: list = field(default_factory=list)
    tasks: list = field(default_factory=list)


def parse(raw: dict) -> Scenario:
    """One scenario from its YAML mapping. The `expect` block is dropped here."""
    if not isinstance(raw, dict) or "id" not in raw or "as_of" not in raw:
        raise ScenarioError("a scenario needs at least an id and an as_of date")
    as_of = str(raw["as_of"])[:10]
    try:
        date.fromisoformat(as_of)
    except ValueError as error:
        raise ScenarioError(f"scenario {raw['id']}: as_of {raw['as_of']!r} is not a date") from error
    return Scenario(id=str(raw["id"]), title=str(raw.get("title") or ""), as_of=as_of,
                    setup=copy.deepcopy(raw.get("setup") or {}), overrides=copy.deepcopy(raw.get("overrides") or {}),
                    people_responses=copy.deepcopy(raw.get("people_responses") or []),
                    tasks=copy.deepcopy(raw.get("tasks") or []))


def load_file(path: Path) -> list[Scenario]:
    """Every scenario in a YAML file (a list of scenarios or one scenario)."""
    raw = read_yaml(path)
    return [parse(item) for item in (raw if isinstance(raw, list) else [raw])]


def load_dir(directory: Path) -> list[Scenario]:
    out = []
    for path in sorted(directory.glob("*.y*ml")):
        out.extend(load_file(path))
    return out


# -- building the rows -------------------------------------------------------------------
def fixtures() -> dict:
    return read_yaml(FIXTURES)


def _normalise(table: str, row: dict) -> dict:
    aliases = ALIASES.get(table, {})
    return {aliases.get(k, k): v for k, v in row.items()}


def row_key(table: str, row: dict) -> str:
    """How overrides name a row (spec §8.1)."""
    if table == "contract_clauses":
        return str(row["clause_id"])
    if table == "vendor_bank_accounts":
        return f"{row['vendor']}:{row['account_last4']}"
    if table == "journal_entries":
        return str(row["entry_id"])
    if table == "period_rates" and "id" not in row:
        return f"{row['pair']}:{row['period']}"
    if "id" in row:
        return str(row["id"])
    if table == "delegations":
        return f"{row['from_person']}:{row['to_person']}"
    if table == "vendor_history":
        return f"{row['vendor']}:{row['kind']}:{row.get('date')}"
    raise ScenarioError(f"a {table} row has no id: {row}")


def _merge(base, change):
    """Overrides change fields; a mapping (params, value, detail) is merged key by key."""
    if isinstance(base, dict) and isinstance(change, dict):
        out = dict(base)
        for key, value in change.items():
            out[key] = _merge(base.get(key), value)
        return out
    return copy.deepcopy(change)


def _period_rates(rows: list[dict]) -> list[dict]:
    """Expand `periods: [first, last]` or `periods: "first .. last"` into one row per period."""
    out = []
    for row in rows:
        spans = row.get("periods")
        if spans is None:
            out.append(row)
            continue
        if isinstance(spans, str):
            spans = [s.strip() for s in spans.split("..")]
        first, last = (str(spans[0]), str(spans[-1]))
        for period in dates.month_range(first, last):
            out.append({k: v for k, v in row.items() if k != "periods"} | {"period": period})
    return out


def _deposit_clause(rows: dict, vendor: str, amount: Decimal) -> dict | None:
    """The deposit clause a paid deposit belongs to: on one of the vendor's contracts, and of the paid amount."""
    contracts = {c["id"] for c in rows["contracts"] if c["vendor"] == vendor}
    clauses = [c for c in rows["contract_clauses"] if c["contract"] in contracts and _clause_type(c) == "deposit"]
    for clause in clauses:
        params = clause.get("params") or {}
        if Decimal(str(params.get("gross", params.get("amount", "0")))) == amount:
            return clause
    return clauses[0] if len(clauses) == 1 else None


def _clause_type(clause: dict) -> str | None:
    return clause_type(clause)


def _expand_deposits(rows: dict, deposits: list[dict]) -> None:
    """Each paid deposit (Appendix A `deposits_paid`) becomes its invoice, lines, payment and the
    correct entries: Dr 1310 (+ Dr 1400 VAT for ENT-UK) / Cr 2000 dated paid_at, and the payment."""
    for d in deposits:
        amount = Decimal(str(d["amount"]))
        clause = _deposit_clause(rows, d["vendor"], amount)
        if clause is None:
            raise ScenarioError(f"deposit {d['invoice']}: no deposit clause of {amount} on a {d['vendor']} contract")
        contract = next(c for c in rows["contracts"] if c["id"] == clause["contract"])
        entity = contract.get("entity") or next(e["entity"] for e in rows["events"] if e["id"] == contract["event"])
        event = next((e for e in rows["events"] if e["id"] == contract.get("event")), None)
        po = d.get("po") or next((p["id"] for p in rows["purchase_orders"]
                                  if p["vendor"] == d["vendor"] and clause["clause_id"] in (p.get("lines") or [])), None)
        paid_at = str(d["paid_at"])
        received = str(d.get("received_at") or (date.fromisoformat(paid_at) - timedelta(days=5)).isoformat())
        currency = d.get("currency") or next(e["currency"] for e in rows["entities"] if e["id"] == entity)
        params = clause.get("params") or {}
        vat = Decimal(str(params["vat"])) if "vat" in params else Decimal("0")
        net = amount - vat
        invoice = d["invoice"]
        rows["invoices"].append({"id": invoice, "vendor": d["vendor"], "entity": entity, "po": po,
                                 "event": contract.get("event"), "contract": contract["id"], "invoice_number": invoice,
                                 "invoice_date": received, "received_at": received, "received_via": "email_pdf",
                                 "currency": currency, "total": amount})
        rows["invoice_lines"].append({"id": f"{invoice}:1", "invoice": invoice, "line_no": 1, "kind": "deposit",
                                      "description": "Deposit", "qty": 1, "unit_price": net, "amount": net,
                                      "clause_ref": clause["clause_id"]})
        if vat:
            rows["invoice_lines"].append({"id": f"{invoice}:2", "invoice": invoice, "line_no": 2, "kind": "vat",
                                          "description": "VAT on deposit", "amount": vat,
                                          "clause_ref": clause["clause_id"]})
        last4 = next(a["account_last4"] for a in rows["vendor_bank_accounts"] if a["vendor"] == d["vendor"])
        rows["payments"].append({"id": f"PAY-{invoice}", "invoice": invoice, "amount": amount, "paid_at": paid_at,
                                 "account_last4": last4})
        tags = {"event": contract.get("event"), "cost_center": event.get("cost_center") if event else None}
        lines = [{"account": "1310", "debit": net, **tags}] + ([{"account": "1400", "debit": vat, **tags}] if vat else [])
        rows["journal_entries"].append({
            "entry_id": f"JE-{invoice}", "entity": entity, "entry_date": paid_at, "type": "ap_deposit",
            "reason": f"Deposit invoice {invoice} to prepaid", "source_ref": invoice,
            "lines": lines + [{"account": "2000", "credit": amount, **tags}]})
        rows["journal_entries"].append({
            "entry_id": f"JE-PAY-{invoice}", "entity": entity, "entry_date": paid_at, "type": "payment",
            "reason": f"Payment of deposit invoice {invoice}", "source_ref": invoice,
            "lines": [{"account": "2000", "debit": amount, **tags}, {"account": "1000", "credit": amount, **tags}]})


def _po_lines(rows: dict) -> None:
    """A PO's `lines: [clause refs]` shorthand becomes po_lines rows."""
    for po in rows["purchase_orders"]:
        for n, ref in enumerate(po.get("lines") or [], 1):
            if isinstance(ref, dict):
                rows["po_lines"].append({"id": f"{po['id']}:{n}", "po": po["id"], **ref})
            else:
                rows["po_lines"].append({"id": f"{po['id']}:{n}", "po": po["id"], "clause_ref": str(ref)})


def _inline_invoice_lines(rows: dict) -> None:
    """An invoice may carry its lines inline (`lines: [...]`)."""
    for invoice in rows["invoices"]:
        for n, line in enumerate(invoice.pop("lines", None) or [], 1):
            line = dict(line)
            line.setdefault("id", f"{invoice['id']}:{line.get('line_no', n)}")
            line.setdefault("line_no", n)
            line["invoice"] = invoice["id"]
            rows["invoice_lines"].append(line)


def _visible(table: str, row: dict, as_of: str) -> bool:
    for column in VISIBLE_BY.get(table, ()):
        value = row.get(column)
        if value not in (None, ""):
            return str(value)[:10] <= as_of
    return True


def _hide_future_fields(row: dict, as_of: str) -> dict:
    """A master-data change requested before as_of whose call-back happened later: the call-back is not yet visible."""
    if row.get("callback_at") and str(row["callback_at"])[:10] > as_of:
        return {**row, "callback_at": None, "callback_by": None, "callback_number": None,
                "changed_by": None, "approved_by": None}
    return row


def rows_for(scenario: Scenario | None = None, as_of: str | None = None) -> tuple[dict, dict]:
    """(rows by table, meta) as they stand on as_of. The scenario's setup is added to the base fixtures,
    its overrides applied, deposits and shorthands expanded, then every later row left out."""
    base = fixtures()
    as_of = as_of or (scenario.as_of if scenario else str(base["as_of"]))
    rows: dict[str, list[dict]] = {t: [_normalise(t, r) for r in base.get(t) or []] for t in TABLES}
    deposits = list(base.get("deposits_paid") or [])
    if scenario:
        unknown = sorted(set(scenario.setup) - set(TABLES) - {"deposits_paid"})
        if unknown:
            raise ScenarioError(f"scenario {scenario.id}: unknown setup tables {unknown}")
        for table, added in scenario.setup.items():
            if table == "deposits_paid":
                deposits += list(added or [])
            else:
                rows[table] += [_normalise(table, r) for r in added or []]
    rows["period_rates"] = _period_rates(rows["period_rates"])
    _inline_invoice_lines(rows)
    _po_lines(rows)
    _expand_deposits(rows, deposits)
    if scenario:
        for table, changes in scenario.overrides.items():
            if table not in TABLES:
                raise ScenarioError(f"scenario {scenario.id}: cannot override unknown table {table}")
            index = {row_key(table, r): i for i, r in enumerate(rows[table])}
            for key, change in (changes or {}).items():
                if str(key) not in index:
                    raise ScenarioError(f"scenario {scenario.id}: override names no {table} row {key!r}")
                i = index[str(key)]
                rows[table][i] = _merge(rows[table][i], _normalise(table, change))
    for clause in rows["contract_clauses"]:
        clause["type"] = clause_type(clause)
    for table in TABLES:
        rows[table] = [_hide_future_fields(r, as_of) for r in rows[table] if _visible(table, r, as_of)]
    visible_entries = {r["entry_id"] for r in rows["journal_entries"]}
    rows["journal_lines"] = [r for r in rows["journal_lines"] if r.get("entry_id") in visible_entries]
    visible_invoices = {r["id"] for r in rows["invoices"]}
    rows["invoice_lines"] = [r for r in rows["invoice_lines"] if r["invoice"] in visible_invoices]
    meta = {"as_of": as_of, "scenario_id": scenario.id if scenario else "base",
            "title": scenario.title if scenario else "Base company", "first_period": str(base["periods"]["first"])}
    return rows, meta


def to_text(value) -> str | None:
    """How a value is stored: amounts as exact decimal text, mappings and lists as JSON."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, sort_keys=True)
    return str(value)
