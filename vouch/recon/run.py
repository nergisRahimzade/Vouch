"""Deterministic pre-pass. The agent chooses actions; this layer owns every amount."""
from dataclasses import asdict
from decimal import Decimal

from vouch.calc import eventtax as et
from vouch.calc import ledgers as lg
from vouch.controls.matrix import route
from vouch.money import ZERO, cents, fmt
from vouch.recon.checks import detect
from vouch.recon.sources import Sources


MONEY_FIELDS = ("deductible", "nondeductible", "vat_recoverable", "vat_blocked", "wage_taxable",
                "flat_tax_40", "flat_tax_37b")


def plain(value):
    if isinstance(value, Decimal):
        return fmt(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    return value


def classify(src, event, invoices, attendees):
    categories = {l.get("category") for i in invoices for l in i["lines"]}
    if categories and categories <= {"gift", "promo_item"}:
        return "promo_item" if all(l.get("unit_net") is not None and et.is_promo_item(
            Decimal(str(l["unit_net"])), src.rules, event["date"]) for i in invoices for l in i["lines"]) else "gift"
    if not attendees:
        return event.get("type_declared") or "hospitality"
    employees = sum(a["person_kind"] == "employee" for a in attendees)
    companions = sum(a["person_kind"] == "companion" for a in attendees)
    others = len(attendees) - employees - companions
    if event.get("exceptional_assignment") and employees and not others and not companions:
        gross = sum((Decimal(str(i.get("gross") or 0)) for i in invoices), ZERO)
        return "working_meal" if et.working_meal_test(gross, employees, src.rules, event["date"]).values[
            "within_limit"] else "working_meal_over_limit"
    openness = event.get("open_to")
    open_to_all = openness == "all" or (isinstance(openness, str) and openness in src.units)
    facts = et.qualifies_as_staff_event(open_to_all, employees, companions, others,
                                       src.rules, event["date"]).values
    if facts["staff_majority"]:
        return "staff_event" if facts["qualifies"] else "staff_event_nonqualifying"
    return "mixed" if categories & {"program", "package"} else "hospitality"


def staff_state(src, year):
    attrs = []
    for event in src.events.values():
        if event["date"][:4] != str(year) or event["date"] > src.as_of:
            continue
        attendees, invoices = src.attendance(event["id"]), src.invoices(event["id"])
        if not attendees or not invoices:
            continue
        cls = classify(src, event, invoices, attendees)
        if cls not in {"staff_event", "staff_event_nonqualifying"}:
            continue
        gross = sum((Decimal(str(i["gross"])) for i in invoices), ZERO)
        allocation = et.allocate_staff_event(gross, attendees, src.rules, event["date"])
        attrs.append(lg.EventAttribution(event["id"], event["date"], cls == "staff_event",
                                        allocation.values["attributed"]))
    return lg.staff_ledger(attrs, src.rules)


def visible_gifts(src, year):
    """Allocate visible gift lines by recorded quantities; return missing recipient invoice IDs."""
    gift_rows, missing_invoices = [], set()
    invoice_by_id = {i["id"]: i for i in src.data["invoices"]}
    for line in src.data["invoice_lines"]:
        if line["category"] != "gift":
            continue
        inv = invoice_by_id[line["invoice"]]
        day = inv.get("delivery_date") or inv.get("invoice_date") or src.as_of
        if day[:4] != str(year):
            continue
        distributions = [d for d in src.data["gift_distributions"] if d["invoice_line"] == line["id"]]
        if not distributions and line.get("recipient"):
            distributions = [{"id": line["id"], "recipient": line["recipient"], "qty": line.get("qty") or 1}]
        quantity = Decimal(str(line.get("qty") or 1))
        if quantity <= 0:
            raise ValueError("gift quantity must be positive")
        unit_net = Decimal(str(line.get("unit_net") or Decimal(line["net"]) / quantity))
        if et.is_promo_item(unit_net, src.rules, day):
            continue
        named_quantity = sum((Decimal(str(d.get("qty") or 1)) for d in distributions if d.get("recipient")), ZERO)
        if named_quantity != quantity:
            missing_invoices.add(inv["id"])
        allocated_net = allocated_vat = ZERO
        for n, d in enumerate(distributions):
            if not d.get("recipient"):
                continue
            share = Decimal(str(d.get("qty") or 1)) / quantity
            gn, gv = cents(Decimal(line["net"]) * share), cents(Decimal(line["vat"]) * share)
            if n == len(distributions) - 1 and named_quantity == quantity:
                gn, gv = Decimal(line["net"]) - allocated_net, Decimal(line["vat"]) - allocated_vat
            allocated_net += gn
            allocated_vat += gv
            gid = line["id"] if len(distributions) == 1 else d["id"]
            gift_rows.append((et.Gift(gid, day, gn, gv, gn + gv, d["recipient"]), inv["id"], line["id"]))
    return gift_rows, missing_invoices


def benefit_totals(src, year):
    """The ?37b annual cap combines gifts and customer-program benefits for each person."""
    gift_rows, _ = visible_gifts(src, year)
    totals = {}
    for gift, _, _ in gift_rows:
        totals[gift.recipient] = totals.get(gift.recipient, ZERO) + gift.gross
    for event in src.events.values():
        if event["date"][:4] != str(year) or event["date"] > src.as_of:
            continue
        invoices, attendance = src.invoices(event["id"]), src.attendance(event["id"])
        if not attendance or classify(src, event, invoices, attendance) != "mixed":
            continue
        gross = sum((Decimal(line["gross"]) for invoice in invoices for line in invoice["lines"]
                     if line.get("category") == "program"), ZERO)
        for person in attendance:
            if person["person_kind"] == "business_partner":
                pid = person["person_id"]
                totals[pid] = totals.get(pid, ZERO) + et.share_by_headcount(gross, 1, len(attendance))
    return totals


def prepass(conn, task, source=None):
    src = source or Sources(conn)
    kind = task.get("type") or task.get("kind") or "process_event"
    target = task.get("event") or task.get("event_id") or task.get("invoice") or task.get("invoice_id") or task.get("target") or task.get("id")
    invoice = next((i for i in src.data["invoices"] if i["id"] == target), None)
    event_id = invoice["matched_event"] if invoice else target
    event = src.events.get(event_id)
    if event is None and invoice and all(l.get("category") in {"gift", "promo_item"} for l in invoice["lines"]):
        event = {"id": None, "date": invoice.get("delivery_date") or invoice.get("invoice_date") or src.as_of,
                 "organizer": invoice.get("organizer"), "cost_center": invoice.get("cost_center") or "CC-ADMIN"}
    if event is None:
        raise ValueError("trigger has no unambiguous visible event; provide an explicit event or dated evidence")
    invoices = src.invoices(event_id) if event_id else [invoice]
    if kind == "process_invoice" and invoice and not event_id:
        invoices = [invoice]
    attendees = src.attendance(event_id) if event_id else []
    classification = classify(src, event, invoices, attendees)
    booked_ids = src.booked_invoices()
    new = [i for i in invoices if i["id"] not in booked_ids]
    result = {"case_id": str(target), "task": str(target), "decision": "classify_and_book",
              "classification": classification, "matched_event": event_id,
              **{k: ZERO for k in MONEY_FIELDS}, "payroll_method": "none", "corrections": [],
              "flags": [], "approvers": [], "escalations": [], "requests": [], "entries": [],
              "cites": [], "rationale": "Computed from visible sources and versioned rules."}
    rules_used, policies, treatments, payroll = set(), set(), {}, []
    docs, reassigned, missing_gifts, cap_exceeded, gift_exceeded = {}, [], False, False, False
    net = sum((Decimal(str(i["net"])) for i in new), ZERO)
    vat = sum((Decimal(str(i["vat"])) for i in new), ZERO)
    gross = sum((Decimal(str(i["gross"])) for i in new), ZERO)
    problems = et.vat_arithmetic([l for i in invoices for l in i["lines"]])
    problems += et.vat_arithmetic(invoices)

    def use(calc):
        rules_used.update(calc.rules)
        policies.update(calc.policies)
        return calc.values

    if not invoices:
        result["decision"] = "hold"
        result["requests"].append({"to": event.get("organizer"), "about": "invoices"})
    elif problems:
        result["decision"] = "hold"
        result["rationale"] = "Invoice arithmetic does not reconcile: " + "; ".join(problems)
        result["requests"].append({"to": invoices[0].get("vendor"), "about": "invoice_arithmetic"})
        rules_used.add(src.rules.cite("TAX-VAT", src.as_of))
    elif classification not in {"gift", "promo_item"} and not attendees:
        result["decision"] = "hold"
    elif classification in {"staff_event", "staff_event_nonqualifying"}:
        allocation = use(et.allocate_staff_event(sum((Decimal(str(i["gross"])) for i in invoices), ZERO),
                                                attendees, src.rules, event["date"]))
        result["per_participant"] = allocation["per_participant_rounded"]
        ledger = staff_state(src, int(event["date"][:4]))
        rules_used.update(ledger.rules)
        policies.update(ledger.policies)
        booked_events = {l["event"] for l in src.booked_lines() if l["event"] and l["account"] == "1600"}
        previous = {}
        for row in src.data["payroll_exports"]:
            if row["status"] == "accepted" and row["period"][:4] == event["date"][:4]:
                key = (row["employee"], row["event"])
                previous[key] = previous.get(key, ZERO) + Decimal(row["taxable_amount"])
                booked_events.add(row["event"])
        changes = lg.payroll_changes(ledger, previous, booked_events, {event_id}, src.rules)
        reassigned = changes.reassigned
        result["wage_taxable"] = changes.new_total + changes.delta_total
        result["corrections"] = [asdict(c) for c in changes.corrections]
        for rows, correction in ((changes.new, False), (changes.deltas, True)):
            payroll += [{"employee": person, "event": ev, "taxable_amount": value,
                         "method": "flat_40", "corrects": ev if correction else None}
                        for (person, ev), value in rows.items()]
        recoverable = use(et.staff_event_vat_test(allocation["per_participant"], src.rules, event["date"]))["recoverable"]
        if classification == "staff_event_nonqualifying":
            recoverable = False
            ruling = None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='learned_corrections'").fetchone():
                ruling = conn.execute("SELECT method FROM learned_corrections WHERE case_id=? ORDER BY at DESC LIMIT 1", (str(target),)).fetchone()
            if ruling:
                method = ruling[0]
                result["payroll_method"] = method
                payroll = [{**p, "method": method} for p in payroll]
                if method == "flat_40":
                    result["flat_tax_40"] = use(et.flat_tax_40(result["wage_taxable"], src.rules, src.as_of))["total"]
                payroll_id = next(p["id"] for p in src.data["people"] if p["role"] == "payroll")
                result["requests"].append({"to": payroll_id, "about": "payroll_export"})
            else:
                result["decision"] = "escalate"
                result["escalations"] = ["tax_manager"]
                payroll = []
            policies.add("POL-BV-FLAT")
        else:
            result["corrections"] += [asdict(c) for c in lg.vat_correction(event_id, recoverable,
                                          src.net_booked("1400", event=event_id), src.rules, src.as_of)]
            if result["wage_taxable"]:
                result["flat_tax_40"] = use(et.flat_tax_40(result["wage_taxable"], src.rules, src.as_of))["total"]
                result["payroll_method"] = "flat_40"
                payroll_id = next(p["id"] for p in src.data["people"] if p["role"] == "payroll")
                result["requests"].append({"to": payroll_id, "about": "payroll_export"})
        result["vat_recoverable"], result["vat_blocked"] = (vat, ZERO) if recoverable else (ZERO, vat)
        result["deductible"] = net + result["vat_blocked"]
        for i in new:
            treatments[i["id"]] = {"classification": classification, "deductible": Decimal(i["net"]) + (ZERO if recoverable else Decimal(i["vat"])),
                                    "vat_recoverable": Decimal(i["vat"]) if recoverable else ZERO,
                                    "vat_blocked": ZERO if recoverable else Decimal(i["vat"])}
        if event_id in booked_events and result["corrections"]:
            result["decision"] = "post_corrections"
    elif classification in {"hospitality", "mixed"}:
        known_names = {}
        for person in src.data["employees"] + src.data["business_partners"]:
            known_names.setdefault(person["name"], set()).add(person["id"])
        for i in invoices:
            named = set()
            for person in i.get("participants") or []:
                named.add(person)
                named.update(known_names.get(person, set()))
            all_named = bool(attendees) and {a["person_id"] for a in attendees} <= named
            docs[i["id"]] = use(et.hospitality_docs(i.get("occasion") or event.get("purpose"),
                   all_named, i.get("host") or event.get("host"), Decimal(i["gross"]),
                   bool(i.get("invoice_date") or i.get("delivery_date")), bool(i.get("place") or event.get("location")),
                   src.rules, event["date"]))
        if not all(d["complete"] for d in docs.values()):
            result["decision"] = "hold"
        elif any(l["category"] == "package" for i in invoices for l in i["lines"]):
            result["decision"] = "hold"
        else:
            program_net = program_gross = ZERO
            for i in new:
                pn = sum((Decimal(l["net"]) for l in i["lines"] if l["category"] == "program"), ZERO)
                pg = sum((Decimal(l["gross"]) for l in i["lines"] if l["category"] == "program"), ZERO)
                split = use(et.hospitality_split(Decimal(i["net"]) - pn, src.rules, event["date"]))
                treatments[i["id"]] = {"classification": classification, "deductible": split["deductible"],
                                        "nondeductible": split["nondeductible"], "program_deductible": pn,
                                        "vat_recoverable": Decimal(i["vat"])}
                result["deductible"] += split["deductible"]
                result["nondeductible"] += split["nondeductible"]
                program_net += pn
                program_gross += pg
            result["vat_recoverable"] = vat
            if program_net:
                result["program_deductible"] = program_net
                policies.add("POL-BEW-02")
                partners = sum(a["person_kind"] == "business_partner" for a in attendees)
                base = et.share_by_headcount(program_gross, partners, len(attendees))
                if src.election(int(event["date"][:4])):
                    totals = benefit_totals(src, int(event["date"][:4]))
                    cap_exceeded = any(not et.flat_tax_37b_available(totals.get(a["person_id"], ZERO), src.rules, src.as_of).values["available"]
                                       for a in attendees if a["person_kind"] == "business_partner")
                    if cap_exceeded:
                        result["decision"], result["escalations"] = "escalate", ["tax_manager"]
                    else:
                        result["flat_tax_37b"] = use(et.flat_tax_37b(base, src.rules, src.as_of))["total"]
    elif classification in {"working_meal", "working_meal_over_limit", "promo_item"}:
        result["deductible"], result["vat_recoverable"] = net, vat
        rules_used.add(src.rules.cite("R-PROMO" if classification == "promo_item" else "R-AE-60", event["date"]))
        if classification == "working_meal_over_limit":
            result["wage_taxable"], result["payroll_method"] = gross, "individual"
            employees = [a for a in attendees if a["person_kind"] == "employee"]
            # Allocate the final rounding cent deterministically without inventing payroll value.
            remaining = gross
            for n, a in enumerate(employees):
                amount = cents(gross / len(employees)) if n < len(employees) - 1 else remaining
                remaining -= amount
                payroll.append({"employee": a["person_id"], "event": event_id, "taxable_amount": amount,
                                "method": "individual"})
        treatments = {i["id"]: {"classification": classification, "deductible": Decimal(i["net"]),
                               "vat_recoverable": Decimal(i["vat"])} for i in new}
    elif classification == "gift":
        gift_rows, missing_invoices = visible_gifts(src, int(event["date"][:4]))
        missing_gifts = bool({i["id"] for i in invoices} & missing_invoices)
        if missing_gifts:
            if any(i.get("unknown_recipients_confirmed") for i in invoices):
                result["decision"], result["escalations"] = "escalate", ["tax_manager"]
            else:
                result["decision"] = "hold"
        else:
            deductible_base = nondeductible_base = ZERO
            new_ids = {i["id"] for i in new}
            annual_totals = benefit_totals(src, int(event["date"][:4]))
            for person in sorted({g.recipient for g, _, _ in gift_rows}):
                person_rows = [(g, inv, line) for g, inv, line in gift_rows if g.recipient == person]
                ledger = et.gift_ledger([g for g, _, _ in person_rows], int(event["date"][:4]), src.rules)
                use(ledger)
                if not ledger.values["deductible"] and any(inv in new_ids for _, inv, _ in person_rows):
                    gift_exceeded = True
                annual = annual_totals.get(person, ZERO)
                available = (not src.election(int(event["date"][:4])) or
                             use(et.flat_tax_37b_available(annual, src.rules, src.as_of))["available"])
                if not available:
                    cap_exceeded = True
                    continue
                previous = []
                for g, inv_id, line_id in person_rows:
                    treatment = ledger.values["items"][g.id]
                    if inv_id in new_ids:
                        for field in ("deductible", "nondeductible"):
                            result[field] += treatment[field + "_amount"]
                        for field in ("vat_recoverable", "vat_blocked"):
                            result[field] += treatment[field]
                        t = treatments.setdefault(inv_id, {"classification": "gift", **{k: ZERO for k in MONEY_FIELDS}})
                        for field in ("deductible", "nondeductible"):
                            t[field] += treatment[field + "_amount"]
                        for field in ("vat_recoverable", "vat_blocked"):
                            t[field] += treatment[field]
                        if treatment["deductible"]:
                            deductible_base += g.gross
                        else:
                            nondeductible_base += g.gross
                    elif inv_id in booked_ids:
                        booked_lines = [l for l in src.booked_lines(item=line_id) if l.get("person") in (None, person)]
                        recovered = sum((Decimal(l["debit"]) - Decimal(l["credit"]) for l in booked_lines if l["account"] == "1400"), ZERO)
                        ded = sum((Decimal(l["debit"]) - Decimal(l["credit"]) for l in booked_lines if l["account"] == "6420"), ZERO) > 0
                        flat = sum((Decimal(l["debit"]) - Decimal(l["credit"]) for l in booked_lines if l["account"] in {"6450", "6460"}), ZERO)
                        previous.append(lg.BookedGift(g.id, person, ded, g.net, recovered, flat, "6450" if ded else "6460"))
                result["corrections"] += [asdict(c) for c in lg.gift_corrections(ledger, previous, src.rules, src.as_of)]
            if src.election(int(event["date"][:4])):
                # POL-ROUND: tax on the summed run base, then split its expense by benefit deductibility.
                total_base = deductible_base + nondeductible_base
                result["flat_tax_37b"] = use(et.flat_tax_37b(total_base, src.rules, src.as_of))["total"]
                result["flat_37b_nondeductible"] = cents(result["flat_tax_37b"] * nondeductible_base / total_base) if total_base else ZERO
            if result["corrections"]:
                result["decision"] = "post_corrections"
            if cap_exceeded:
                result["decision"], result["escalations"] = "escalate", ["tax_manager"]
    findings = detect(event, new, attendees, src.rules, src.as_of, classification=classification,
                      corrections=result["corrections"], reassigned=reassigned, gift_missing=missing_gifts,
                      cap_exceeded=cap_exceeded, gift_exceeded=gift_exceeded, emails=src.data["emails"], docs=docs,
                      all_invoices=src.data["invoices"], locked=src.locked)
    for correction in result["corrections"]:
        rules_used.update(correction.get("rules", []))
    result["flags"] = sorted({f.kind for f in findings})
    for finding in findings:
        rules_used.update(finding.rules)
        policies.update(finding.policies)
        if finding.request and finding.request not in result["requests"]:
            if finding.request["to"] != "tax_manager":
                result["requests"].append(finding.request)
    if "duplicate_invoice" in result["flags"]:
        result["decision"] = "reject_duplicate"
    if "lumped_invoice" in result["flags"]:
        result["decision"] = "hold"
    if "election_change_request" in result["flags"]:
        result["escalations"] = ["tax_manager"]
    if not new and not result["corrections"] and result["decision"] == "classify_and_book":
        result["decision"] = "no_action"
    blocked = result["decision"] in {"hold", "escalate", "reject_duplicate", "no_action"}
    routing = route(flags=result["flags"], wage_taxable=result["wage_taxable"], nondeductible=result["nondeductible"],
                    flat_tax_37b=result["flat_tax_37b"], corrections=result["corrections"],
                    escalations=result["escalations"], blocked=blocked)
    result["approvers"] = routing["approvers"]
    policies.add("POL-MAT")
    result["cites"] = sorted({r.split(" v")[0] for r in rules_used} | policies |
                              {i["id"] for i in invoices} | ({event_id} if event_id else set()))
    return plain({"decision": result, "event": event, "invoices": invoices, "new_invoices": new,
                  "attendance": attendees, "treatments": treatments, "payroll": payroll if not blocked else [],
                  "findings": [f.to_record() for f in findings], "rules": sorted(rules_used),
                  "policies": sorted(policies), "arithmetic_problems": problems})
