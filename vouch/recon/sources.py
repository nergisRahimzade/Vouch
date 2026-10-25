"""Read the visible system of record, deriving tags, invoice matches and booked state."""
import json
from decimal import Decimal

from vouch.calc.eventtax import Rules
from vouch.db import periods
from vouch.world.scenario import event_cost_center
from vouch.world.generator import expand

TABLES = {"entities", "cost_centers", "org_units", "employees", "people", "delegations", "business_partners",
          "vendors", "events", "attendance", "gift_distributions", "invoices", "invoice_lines", "emails",
          "elections", "payroll_exports", "recipient_notices", "rules", "policies", "journal_entries", "journal_lines"}


class Sources:
    def __init__(self, conn):
        self.conn = conn
        self.as_of = periods.as_of(conn).isoformat()
        self.open_month = self.as_of[:7]
        self.data = {t: self.rows(t) for t in TABLES}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'received_facts'").fetchone():
            self.apply_received_facts()
        self.rules = Rules(self.data["rules"])
        self.events = {r["id"]: r for r in self.data["events"]}
        self.people = {r["id"]: r for r in self.data["people"]}
        self.units = {r["id"]: expand(r.get("members")) for r in self.data["cost_centers"] + self.data["org_units"]}
        units = {e["id"]: e["cost_center"] for e in self.data["employees"]}
        for event in self.events.values():
            event["cost_center"] = event_cost_center(event, self.units, units, self.people)
        self.locked = frozenset(r[0] for r in conn.execute("SELECT period FROM periods WHERE status = 'locked'"))
        entities = self.data["entities"]
        for invoice in self.data["invoices"]:
            if not invoice.get("entity") and len(entities) == 1:
                invoice["entity"] = entities[0]["id"]
            invoice["lines"] = [r for r in self.data["invoice_lines"] if r["invoice"] == invoice["id"]]
            invoice["matched_event"], invoice["match_evidence"] = self.match(invoice)

    def apply_received_facts(self):
        """Overlay attributed responses; original invoice/attendance rows remain immutable."""
        replies = [dict(r) for r in self.conn.execute("SELECT q.case_id,q.about,f.data,f.request_id,f.received_at,0 AS ordinal "
                   "FROM received_facts f JOIN info_requests q USING(request_id)")]
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='followup_facts'").fetchone():
            replies += [dict(r) for r in self.conn.execute("SELECT q.case_id,q.about,f.data,f.request_id,f.received_at,f.id AS ordinal "
                       "FROM followup_facts f JOIN info_requests q USING(request_id)")]
        for row in sorted(replies,key=lambda r:(r["received_at"],r["request_id"],r["ordinal"])):
            data = json.loads(row["data"])
            if row["about"] == "attendance":
                from vouch.world.scenario import _attendance
                actual = [{**a,"source_request":row["request_id"]} for a in _attendance([{**data, "event":row["case_id"]}])]
                known = {p["id"] for p in self.data["employees"] + self.data["business_partners"]}
                if not actual or any(p["person_id"] not in known for p in actual if p["person_kind"] != "companion"):
                    raise ValueError("attendance reply must identify actual recorded people")
                self.data["attendance"] = [a for a in self.data["attendance"] if a["event"] != row["case_id"]] + actual
            invoices = [i for i in self.data["invoices"] if i.get("event") == row["case_id"] or i["id"] == row["case_id"]]
            for invoice in invoices:
                if row["about"] == "bewirtung_attendees_occasion":
                    invoice.update(data)
                elif row["about"] == "itemized_invoice":
                    replacement = data["lines"]
                    for field in ("net", "vat", "gross"):
                        if sum((Decimal(str(l[field])) for l in replacement), Decimal(0)) != Decimal(invoice[field]):
                            raise ValueError("vendor itemization does not reconcile with original invoice totals")
                    self.data["invoice_lines"] = [l for l in self.data["invoice_lines"] if l["invoice"] != invoice["id"]]
                    self.data["invoice_lines"] += [{**l, "id": f"{invoice['id']}:reply:{n}",
                                                 "invoice": invoice["id"], "source_request": row["request_id"]}
                                                for n, l in enumerate(replacement, 1)]
                elif row["about"] == "gift_recipient_list":
                    lines = [l for l in self.data["invoice_lines"] if l["invoice"] == invoice["id"] and l["category"] == "gift"]
                    if len(lines) != 1:
                        raise ValueError("recipient answer must identify a single gift line")
                    people = expand(data["recipients"])
                    unknown = int(data.get("unknown_count", 0))
                    if len(people) + unknown != int(Decimal(str(lines[0].get("qty") or 1))):
                        raise ValueError("recipient count does not match gift quantity")
                    self.data["gift_distributions"] = [g for g in self.data["gift_distributions"] if g["invoice_line"] != lines[0]["id"]]
                    invoice["unknown_recipients_confirmed"] = unknown > 0
                    if unknown:
                        self.data["gift_distributions"].append({"id": row["request_id"]+":unknown", "invoice":invoice["id"],
                            "invoice_line":lines[0]["id"], "recipient":None,"qty":str(unknown)})
                    self.data["gift_distributions"] += [{"id": f"{row['request_id']}:{n}", "invoice": invoice["id"],
                        "invoice_line": lines[0]["id"], "event": invoice.get("event"), "recipient": person, "qty": "1"}
                        for n, person in enumerate(people, 1)]

    def rows(self, table):
        if table not in TABLES:
            raise ValueError("unknown source table")
        out = []
        for row in self.conn.execute(f"SELECT * FROM {table}"):
            record = dict(row)
            extra = json.loads(record.pop("extra", None) or "{}")
            for key in ("participants", "open_to"):
                value = record.get(key)
                if isinstance(value, str) and value.startswith(("[", "{")):
                    record[key] = json.loads(value)
            out.append({**extra, **record})
        return out

    def attendance(self, event):
        return [r for r in self.data["attendance"] if r["event"] == event]

    def invoices(self, event):
        return [r for r in self.data["invoices"] if r["matched_event"] == event]

    def match(self, invoice):
        explicit = invoice.get("event")
        if explicit in self.events:
            return explicit, [{"method": "event_id", "value": explicit}]
        text = str(invoice.get("event_ref") or "").casefold().strip()
        by_text = [e for e in self.events.values() if text and text in {str(e.get("name") or "").casefold(), e["id"].casefold()}]
        day = str(invoice.get("delivery_date") or "")[:10]
        by_day = [e for e in self.events.values() if day and e["date"] <= day <= (e.get("end_date") or e["date"])]
        if len(by_day) == 1:
            evidence = [{"method": "delivery_date", "value": day}]
            if len(by_text) == 1 and by_text[0]["id"] != by_day[0]["id"]:
                evidence.append({"conflicting_reference": invoice.get("event_ref"), "event": by_text[0]["id"]})
            return by_day[0]["id"], evidence
        if len(by_text) == 1 and not day:
            return by_text[0]["id"], [{"method": "text_reference", "value": text}]
        return None, [{"method": "unresolved", "reference": invoice.get("event_ref"), "delivery_date": day}]

    def booked_lines(self, *, event=None, item=None, posted_only=True):
        entries = {e["entry_id"] for e in self.data["journal_entries"]
                   if not posted_only or e["status"] == "posted"}
        return [l for l in self.data["journal_lines"] if l["entry_id"] in entries
                and (event is None or l["event"] == event) and (item is None or l["item"] == item)]

    def net_booked(self, account, *, event=None, item=None):
        return sum((Decimal(l["debit"]) - Decimal(l["credit"]) for l in self.booked_lines(event=event, item=item)
                    if l["account"] == account), Decimal(0))

    def booked_invoices(self):
        posted = {e["entry_id"]: e for e in self.data["journal_entries"] if e["status"] == "posted"}
        invoices = {i["id"]: i for i in self.data["invoices"]}
        line_invoice = {l["id"]: l["invoice"] for l in self.data["invoice_lines"]}
        booked = {e["source_ref"] for e in posted.values() if e.get("source_ref") in invoices}
        for line in self.data["journal_lines"]:
            entry = posted.get(line["entry_id"])
            if not entry or line["account"] != "1600":
                continue
            item = line.get("item")
            if item in invoices:
                booked.add(item)
            elif item in line_invoice:
                booked.add(line_invoice[item])
            elif line.get("event"):
                # Compact prior event bookings cover only invoices received by that booking date.
                booked.update(i["id"] for i in self.invoices(line["event"])
                              if str(i.get("received_at") or i.get("invoice_date") or "9999")[:10] <= entry["entry_date"])
        return booked

    def election(self, year, scope="third_parties"):
        matches = [r for r in self.data["elections"] if int(r["year"]) == int(year) and r["scope"] == scope]
        return bool(matches and int(matches[-1]["elected"]))
