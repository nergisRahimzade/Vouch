"""The company world as it stands on one date.

Reads the base fixtures (Appendix A) and, for a scenario, adds its `setup` rows and applies its
`overrides` (spec §8.1). Compact forms are expanded first: generated employees and business partners,
attendance ranges with companions, payroll-export ranges, and processed invoice and gift history (which
become the rows and the journal entries booked for them). Then time travel (D19): every row dated after
`as_of` is left out, so nothing downstream can see the future and every status and per-person ledger is
derived from what is visible.

A scenario's `expect` block is the answer key. It is never part of a Scenario: `parse` drops it, and
only the evaluator reads it (vouch.evaluator.answer_key)."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from vouch.world import generator
from vouch.world.generator import expand

FIXTURES = Path(__file__).with_name("fixtures.yaml")
DEFAULT_COST_CENTER = "CC-ADMIN"   # company-level costs nothing assigns to a unit (simulated bookkeeping choice)

# Tables a scenario may add rows to or override (spec §4.1), in load order.
TABLES = ["entities", "cost_centers", "org_units", "employees", "people", "delegations", "business_partners",
          "vendors", "events", "attendance", "gift_distributions", "invoices", "invoice_lines", "emails", "elections",
          "payroll_exports", "recipient_notices", "rules", "policies", "journal_entries", "journal_lines"]
# Compact setup forms, expanded into the tables above.
COMPACT = {"invoices_history", "invoices_unprocessed", "gift_history"}
# Short field names, mapped to column names.
ALIASES = {"delegations": {"from": "from_person", "to": "to_person"},
           "emails": {"from": "from_address", "to": "to_address"},
           "journal_entries": {"id": "entry_id", "date": "entry_date", "memo": "reason", "invoice": "source_ref"},
           "invoices": {"event_id": "event", "number": "invoice_number"},
           "attendance": {"person": "person_id", "kind": "person_kind", "host": "host_employee"},
           "payroll_exports": {"taxable": "taxable_amount"}}
# The date that decides whether a row is visible on as_of. Rows without one are always visible.
VISIBLE_BY = {"invoices": ("received_at", "invoice_date"), "emails": ("received_at",),
              "journal_entries": ("entry_date",), "elections": ("decided_at",), "gift_distributions": ("date",),
              "payroll_exports": ("period",), "recipient_notices": ("date",)}


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


# -- helpers shared with the layers below ----------------------------------------------------
def fixtures() -> dict:
    return read_yaml(FIXTURES)


def event_cost_center(event: dict, units: dict[str, list[str]], employee_units: dict[str, str],
                      people: dict[str, dict]) -> str:
    """The cost center an event's costs carry: its own, else the unit it is open to, else its organizer's
    unit, else the company-level default."""
    if event.get("cost_center"):
        return str(event["cost_center"])
    if str(event.get("open_to") or "") in units and str(event["open_to"]).startswith("CC-"):
        return str(event["open_to"])
    organizer = people.get(str(event.get("organizer") or ""), {})
    if organizer.get("employee_id") in employee_units:
        return employee_units[organizer["employee_id"]]
    return DEFAULT_COST_CENTER


def to_text(value) -> str | None:
    """How a value is stored: amounts as exact decimal text, mappings and lists as JSON, booleans as 0/1."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, sort_keys=True)
    return str(value)


# -- building the rows -------------------------------------------------------------------------
def _normalise(table: str, row: dict) -> dict:
    aliases = ALIASES.get(table, {})
    return {aliases.get(k, k): v for k, v in row.items()}


def row_key(table: str, row: dict) -> str:
    """How overrides name a row (spec §8.1): by id, rules by rule_id (or rule_id:version)."""
    if table == "rules":
        return f"{row['rule_id']}:{row.get('version', 1)}"
    if table == "journal_entries":
        return str(row["entry_id"])
    if "id" in row:
        return str(row["id"])
    raise ScenarioError(f"a {table} row has no id: {row}")


def _merge(base, change):
    """Overrides change fields; a mapping is merged key by key."""
    if isinstance(base, dict) and isinstance(change, dict):
        out = dict(base)
        for key, value in change.items():
            out[key] = _merge(base.get(key), value)
        return out
    return copy.deepcopy(change)


def _attendance(rows: list[dict]) -> list[dict]:
    """Expand compact attendance: {event, employees: "E001-E300", companions: [{host_range, count_each}],
    business_partners: "BP-101-BP-220", externals: [...]} into one row per actual attendee."""
    out = []
    for row in rows:
        if "person_id" in row:
            out.append({"id": f"{row['event']}:{row['person_id']}", **row})
            continue
        event = row["event"]
        for person in expand(row.get("employees")):
            out.append({"id": f"{event}:{person}", "event": event, "person_id": person, "person_kind": "employee"})
        for group in row.get("companions") or []:
            hosts = expand(group.get("host_range") or group.get("hosts") or group.get("host"))
            for host in hosts:
                for n in range(1, int(group.get("count_each", group.get("count", 1))) + 1):
                    person = f"C-{host}-{n}"
                    out.append({"id": f"{event}:{person}", "event": event, "person_id": person,
                                "person_kind": "companion", "host_employee": host})
        for partner in expand(row.get("business_partners")):
            out.append({"id": f"{event}:{partner}", "event": event, "person_id": partner,
                        "person_kind": "business_partner"})
        externals = row.get("externals")
        names = [f"X-{event}-{n}" for n in range(1, int(externals) + 1)] if isinstance(externals, int) \
            else expand(externals)
        for person in names:
            out.append({"id": f"{event}:{person}", "event": event, "person_id": person, "person_kind": "external"})
    return out


def _payroll_exports(rows: list[dict]) -> list[dict]:
    """Expand {event, employees: "E201-E260", taxable_each, period, method} into one row per employee."""
    out = []
    for row in rows:
        if "employees" not in row:
            out.append({"id": row.get("id") or f"PX-{row.get('event')}-{row['employee']}-{row['period']}", **row})
            continue
        for employee in expand(row["employees"]):
            out.append({"id": f"PX-{row.get('event')}-{employee}-{row['period']}", "period": str(row["period"]),
                        "employee": employee, "event": row.get("event"),
                        "taxable_amount": row.get("taxable_each", row.get("taxable_amount")),
                        "method": row.get("method", "flat_40"), "status": row.get("status", "accepted"),
                        "origin": row.get("origin", "history")})
    return out


def _lines(invoice: dict, lines: list[dict]) -> list[dict]:
    out = []
    for n, line in enumerate(lines, 1):
        line = dict(line)
        line.setdefault("line_no", n)
        line.setdefault("id", f"{invoice['id']}:{line['line_no']}")
        line["invoice"] = invoice["id"]
        out.append(line)
    return out


def _sum_lines(invoice: dict, lines: list[dict]) -> None:
    for key in ("net", "vat", "gross"):
        if invoice.get(key) in (None, "") and lines and all(l.get(key) not in (None, "") for l in lines):
            invoice[key] = sum(Decimal(str(l[key])) for l in lines)


class _Tags:
    """Cost centers for history entries, from the rows as they stand."""

    def __init__(self, rows: dict):
        self.units = {u["id"]: expand(u.get("members")) for u in rows["cost_centers"] + rows["org_units"]}
        self.employee_units = {e["id"]: e.get("cost_center") for e in rows["employees"]}
        self.people = {p["id"]: p for p in rows["people"]}
        self.events = {e["id"]: e for e in rows["events"]}

    def of(self, event_id: str | None) -> str:
        event = self.events.get(str(event_id or ""))
        return event_cost_center(event, self.units, self.employee_units, self.people) if event \
            else DEFAULT_COST_CENTER


def _invoices_history(rows: dict, items: list[dict], tags: _Tags) -> None:
    """A processed invoice: its rows, plus the staff-event entry booked for it (Dr expense net and any blocked
    VAT / Dr 1400 recovered VAT / Cr 1600 gross) and, if taxable pay was booked, its flat tax."""
    for item in items:
        item = dict(item)
        done = item.pop("processed", None) or {}
        lines = item.pop("lines", None) or [{"category": item.pop("category", None), "net": item.get("net"),
                                             "vat": item.get("vat"), "gross": item.get("gross"),
                                             "vat_rate": item.pop("vat_rate", None)}]
        rows["invoices"].append(item)
        rows["invoice_lines"] += _lines(item, lines)
        _sum_lines(item, lines)
        if not done:
            continue
        gross = Decimal(str(item["gross"]))
        recovered = Decimal(str(done.get("vat_recoverable", item.get("vat") or 0)))
        account = str(done.get("account", "6440"))
        tag = {"event": item.get("event"), "cost_center": tags.of(item.get("event")), "item": item["id"]}
        lines = [{"account": account, "debit": gross - recovered, **tag}]
        if recovered:
            lines.append({"account": "1400", "debit": recovered, **tag})
        day = str(done.get("entries_date") or item.get("invoice_date"))
        rows["journal_entries"].append({"entry_id": f"JE-{item['id']}", "entry_date": day, "type": "history",
                                        "reason": f"{item['id']} booked before Vouch", "source_ref": item["id"],
                                        "lines": lines + [{"account": "1600", "credit": gross, **tag}]})
        flat = Decimal(str(done.get("flat_tax_40", "0")))
        if flat:
            rows["journal_entries"].append({"entry_id": f"JE-{item['id']}-FLAT", "entry_date": day, "type": "history",
                                            "reason": f"§40 flat tax for {item['id']}", "source_ref": item["id"],
                                            "lines": [{"account": "6470", "debit": flat, **tag},
                                                      {"account": "1750", "credit": flat, **tag}]})


def _gift_history(rows: dict, items: list[dict], tags: _Tags) -> None:
    """A gift booked before Vouch: its invoice, line and distribution, and the entries for its treatment."""
    for gift in items:
        treatment = gift.get("treatment") or {}
        net, vat, gross = (Decimal(str(gift.get(k) or 0)) for k in ("net", "vat", "gross"))
        invoice = {"id": gift["id"], "vendor": gift.get("vendor"), "invoice_date": gift["date"],
                   "received_at": gift["date"], "delivery_date": gift["date"], "net": net, "vat": vat, "gross": gross,
                   "event": gift.get("event")}
        line = {"id": f"{gift['id']}:1", "invoice": gift["id"], "line_no": 1, "category": "gift",
                "description": gift.get("item"), "net": net, "vat": vat, "gross": gross, "qty": 1, "unit_net": net,
                "recipient": gift["recipient"]}
        rows["invoices"].append(invoice)
        rows["invoice_lines"].append(line)
        rows["gift_distributions"].append({"id": f"GD-{gift['id']}", "event": gift.get("event"), "invoice": gift["id"],
                                           "invoice_line": line["id"], "recipient": gift["recipient"], "qty": 1,
                                           "unit_net": net, "date": gift["date"]})
        recovered = Decimal(str(treatment.get("vat_recovered", vat if treatment.get("deductible") else 0)))
        account = str(treatment.get("account", "6420" if treatment.get("deductible") else "6430"))
        tag = {"event": gift.get("event"), "cost_center": tags.of(gift.get("event")), "item": line["id"],
               "person": gift["recipient"]}
        lines = [{"account": account, "debit": gross - recovered, **tag}]
        if recovered:
            lines.append({"account": "1400", "debit": recovered, **tag})
        rows["journal_entries"].append({"entry_id": f"JE-{gift['id']}", "entry_date": gift["date"], "type": "history",
                                        "reason": f"Gift {gift['id']} to {gift['recipient']} booked before Vouch",
                                        "source_ref": gift["id"],
                                        "lines": lines + [{"account": "1600", "credit": gross, **tag}]})
        flat = Decimal(str(treatment.get("flat_37b", "0")))
        if flat:
            rows["journal_entries"].append({
                "entry_id": f"JE-{gift['id']}-37B", "entry_date": gift["date"], "type": "history",
                "reason": f"§37b flat tax on gift {gift['id']}", "source_ref": gift["id"],
                "lines": [{"account": str(treatment.get("flat_account", "6450")), "debit": flat, **tag},
                          {"account": "1760", "credit": flat, **tag}]})


def _visible(table: str, row: dict, as_of: str, event_dates: dict[str, str]) -> bool:
    if table == "attendance":
        return event_dates.get(str(row["event"]), "") <= as_of
    for column in VISIBLE_BY.get(table, ()):
        value = row.get(column)
        if value not in (None, ""):
            value = str(value)
            return value[:7] <= as_of[:7] if len(value) == 7 else value[:10] <= as_of
    return True


def rows_for(scenario: Scenario | None = None, as_of: str | None = None) -> tuple[dict, dict]:
    """(rows by table, meta) as they stand on as_of: base fixtures plus the scenario's setup, compact forms
    expanded, overrides applied, then every later row left out."""
    base = fixtures()
    as_of = as_of or (scenario.as_of if scenario else str(base["as_of"]))
    rows: dict[str, list[dict]] = {t: [] for t in TABLES}
    for table in TABLES:
        given = base.get(table)
        if table == "employees":
            rows[table] = generator.employees(given, base["cost_centers"])
        elif table == "business_partners":
            rows[table] = generator.business_partners(given)
        else:
            rows[table] = [_normalise(table, r) for r in given or []]
    compact = {k: list(base.get(k) or []) for k in COMPACT}
    if scenario:
        unknown = sorted(set(scenario.setup) - set(TABLES) - COMPACT)
        if unknown:
            raise ScenarioError(f"scenario {scenario.id}: unknown setup tables {unknown}")
        for table, added in scenario.setup.items():
            if table in COMPACT:
                compact[table] += list(added or [])
            else:
                rows[table] += [_normalise(table, r) for r in added or []]
    for election in rows["elections"]:
        election.setdefault("id", f"ELEC-{election['year']}-{election['scope']}")
    rows["attendance"] = _attendance(rows["attendance"])
    rows["payroll_exports"] = _payroll_exports(rows["payroll_exports"])
    for invoice in rows["invoices"]:
        inline = invoice.pop("lines", None)
        if inline:
            rows["invoice_lines"] += _lines(invoice, inline)
            _sum_lines(invoice, inline)
    for invoice in compact["invoices_unprocessed"]:
        invoice = dict(invoice)
        inline = invoice.pop("lines", None) or []
        rows["invoices"].append(invoice)
        rows["invoice_lines"] += _lines(invoice, inline)
        _sum_lines(invoice, inline)
    tags = _Tags(rows)
    _invoices_history(rows, compact["invoices_history"], tags)
    _gift_history(rows, compact["gift_history"], tags)
    if scenario:
        for table, changes in scenario.overrides.items():
            if table not in TABLES:
                raise ScenarioError(f"scenario {scenario.id}: cannot override unknown table {table}")
            index = {row_key(table, r): i for i, r in enumerate(rows[table])}
            if table == "rules":
                index.update({str(r["rule_id"]): i for i, r in enumerate(rows[table])
                              if sum(1 for x in rows[table] if x["rule_id"] == r["rule_id"]) == 1})
            for key, change in (changes or {}).items():
                if str(key) not in index:
                    raise ScenarioError(f"scenario {scenario.id}: override names no {table} row {key!r}")
                i = index[str(key)]
                rows[table][i] = _merge(rows[table][i], _normalise(table, change))
    event_dates = {str(e["id"]): str(e["date"])[:10] for e in rows["events"]}
    for table in TABLES:
        rows[table] = [r for r in rows[table] if _visible(table, r, as_of, event_dates)]
    visible_entries = {r["entry_id"] for r in rows["journal_entries"]}
    rows["journal_lines"] = [r for r in rows["journal_lines"] if r.get("entry_id") in visible_entries]
    visible_invoices = {r["id"] for r in rows["invoices"]}
    rows["invoice_lines"] = [r for r in rows["invoice_lines"] if r["invoice"] in visible_invoices]
    rows["gift_distributions"] = [r for r in rows["gift_distributions"]
                                  if not r.get("invoice") or r["invoice"] in visible_invoices]
    meta = {"as_of": as_of, "scenario_id": scenario.id if scenario else "base",
            "title": scenario.title if scenario else "Base company", "first_period": str(base["periods"]["first"])}
    return rows, meta
