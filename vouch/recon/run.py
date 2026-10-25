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
# Why a computed option cannot be booked yet, by the finding that blocks it.
BLOCKERS = {"attendance_missing": "actual attendance is missing (never estimated)",
            "bewirtung_docs_missing": "hospitality documentation is incomplete (R-BEW-DOC)",
            "lumped_invoice": "a package line must be itemized by the vendor (POL-LUMP)",
            "gift_recipients_missing": "gift recipients are not named (R-GIFT-REC)",
            "flat_tax_cap_exceeded": "a recipient exceeds the §37b cap; the tax manager decides (R-37B-CAP)",
            "duplicate_invoice": "this invoice was already received"}
# Findings raised at intake (stage 2): a rejected duplicate keeps only these.
INTAKE_FLAGS = {"duplicate_invoice", "injection_in_document", "injection_in_email", "election_change_request"}


def plain(value):
    if isinstance(value, Decimal):
        return fmt(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    return value


CLASSIFICATIONS = ("staff_event", "staff_event_nonqualifying", "hospitality", "mixed", "working_meal",
                   "working_meal_over_limit", "gift", "promo_item")


def event_facts(src, event, invoices, attendees):
    """The facts the classification rules test, as data the agent can read and cite."""
    categories = sorted({str(l.get("category")) for i in invoices for l in i["lines"] if l.get("category")})
    kinds = {k: sum(a["person_kind"] == k for a in attendees) for k in ("employee", "companion", "business_partner", "external")}
    openness = event.get("open_to")
    open_to_all = openness == "all" or (isinstance(openness, str) and openness in src.units)
    others = kinds["business_partner"] + kinds["external"]
    staff = et.qualifies_as_staff_event(open_to_all, kinds["employee"], kinds["companion"], others, src.rules,
                                        event["date"]).values if attendees else None
    # An exceptional work assignment is a fact the organizer states: explicitly, or by declaring a working
    # meal for an employees-only event that is not open to a whole unit (then it cannot be a staff party).
    exceptional = bool(event.get("exceptional_assignment")) or (
        event.get("type_declared") == "working_meal" and not open_to_all)
    gross = sum((Decimal(str(i.get("gross") or 0)) for i in invoices), ZERO)
    meal = et.working_meal_test(gross, kinds["employee"], src.rules, event["date"]).values \
        if kinds["employee"] else None
    units = [Decimal(str(l["unit_net"])) for i in invoices for l in i["lines"] if l.get("unit_net") is not None]
    return {"organizer_label": event.get("type_declared"), "open_to": openness, "open_to_whole_unit": open_to_all,
            "purpose": event.get("purpose"), "attendees": kinds, "invoice_categories": categories,
            "staff_share": str(cents(staff["staff_share"])) if staff else None,
            "staff_majority": staff["staff_majority"] if staff else None,
            "exceptional_assignment": exceptional,
            "working_meal_per_person": fmt(meal["per_person"]) if meal else None,
            "working_meal_within_limit": meal["within_limit"] if meal else None,
            "promo_units_within_limit": bool(units) and all(et.is_promo_item(u, src.rules, event["date"]) for u in units)}


def classify(src, event, invoices, attendees):
    f = event_facts(src, event, invoices, attendees)
    categories = set(f["invoice_categories"])
    if categories and categories <= {"gift", "promo_item"}:
        lines = [l for i in invoices for l in i["lines"]]
        return "promo_item" if all(l.get("unit_net") is not None for l in lines) and f["promo_units_within_limit"] \
            else "gift"
    if not attendees:
        return event.get("type_declared") or "hospitality"
    k = f["attendees"]
    if f["exceptional_assignment"] and k["employee"] and not k["business_partner"] + k["external"] + k["companion"]:
        return "working_meal" if f["working_meal_within_limit"] else "working_meal_over_limit"
    if f["staff_majority"]:
        return "staff_event" if f["open_to_whole_unit"] else "staff_event_nonqualifying"
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


def handover_drafts(src, new, treatments):
    """Work a colleague handed over half done: an unposted, unvoided draft someone else keyed for an invoice this
    run books. Each is compared, account by account, with the computed treatment."""
    from vouch.agent.templates import invoice_amounts
    voided = {r[0] for r in src.conn.execute("SELECT entry_id FROM entry_voids")} \
        if src.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'entry_voids'").fetchone() else set()
    drafts = {e["entry_id"]: e for e in src.data["journal_entries"]
              if e["status"] == "draft" and e["origin"] == "history" and e["entry_id"] not in voided}
    out = {}
    for invoice in new:
        for entry_id, entry in drafts.items():
            lines = [l for l in src.data["journal_lines"] if l["entry_id"] == entry_id]
            if entry.get("source_ref") != invoice["id"] and not any(l.get("item") == invoice["id"] for l in lines):
                continue
            keyed = {}
            for line in lines:
                keyed[line["account"]] = keyed.get(line["account"], ZERO) + Decimal(line["debit"]) - Decimal(line["credit"])
            treatment = treatments.get(invoice["id"])
            computed = {a: v for a, v in invoice_amounts(invoice, treatment).items() if v} if treatment else None
            differences = [] if computed is None else [
                {"account": a, "colleague": fmt(keyed.get(a, ZERO)), "computed": fmt(computed.get(a, ZERO))}
                for a in sorted(set(keyed) | set(computed)) if keyed.get(a, ZERO) != computed.get(a, ZERO)]
            out[invoice["id"]] = {"entry_id": entry_id, "drafted_by": entry["created_by"],
                                  "matches": computed is not None and not differences, "differences": differences,
                                  "checked": computed is not None}
    return out


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


def prepass(conn, task, source=None, classification=None):
    """Everything code owns for one trigger. With `classification`, the amounts and blockers that follow if
    that classification is adopted (an option the agent may choose); without it, the classification the
    rule tests indicate (code's recommendation, never shown to the agent as a decision)."""
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
    if classification is not None and classification not in CLASSIFICATIONS:
        raise ValueError(f"unknown classification {classification!r}")
    classification = classification or classify(src, event, invoices, attendees)
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
        if classification == "staff_event" and new:
            prior_invoices = [i for i in invoices if i["id"] in booked_ids]
            if prior_invoices:
                prior_gross = sum((Decimal(str(i["gross"])) for i in prior_invoices), ZERO)
                prior_allocation = et.allocate_staff_event(prior_gross, attendees, src.rules, event["date"]).values
                before_vat = et.staff_event_vat_test(prior_allocation["per_participant"], src.rules, event["date"]).values
                after_vat = et.staff_event_vat_test(allocation["per_participant"], src.rules, event["date"]).values
                result["cost_transition"] = {
                    "before_per_participant": prior_allocation["per_participant_rounded"],
                    "after_per_participant": allocation["per_participant_rounded"],
                    "before_gross": prior_gross,
                    "after_gross": sum((Decimal(str(i["gross"])) for i in invoices), ZERO),
                    "participants": len(attendees),
                    "prior_invoice_ids": [i["id"] for i in prior_invoices],
                    "new_invoice_ids": [i["id"] for i in new],
                    "vat_before_recoverable": before_vat["recoverable"],
                    "vat_after_recoverable": after_vat["recoverable"],
                    "basis": "Previously booked invoices versus all visible invoices, using the current confirmed attendance."}
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
        packaged = [i for i in invoices if any(l["category"] == "package" for l in i["lines"])]
        if not packaged:
            # Stage 8 (documentation) follows stage 4 (itemization): a package is itemized first.
            known_names = {}
            for person in src.data["employees"] + src.data["business_partners"]:
                known_names.setdefault(person["name"], set()).add(person["id"])
            for i in invoices:
                named = set()
                for person in i.get("participants") or []:
                    named.add(person)
                    named.update(known_names.get(person, set()))
                receipt_names_all = str(i.get("participants_named")).lower() in {"1", "true", "yes"}
                all_named = bool(attendees) and (receipt_names_all or {a["person_id"] for a in attendees} <= named)
                docs[i["id"]] = use(et.hospitality_docs(i.get("occasion") or event.get("purpose"),
                       all_named, i.get("host") or event.get("host"), Decimal(i["gross"]),
                       bool(i.get("invoice_date") or i.get("delivery_date")), bool(i.get("place") or event.get("location")),
                       src.rules, event["date"]))
        if packaged:
            policies.add("POL-LUMP")
            if any(i.get("itemization_declined") for i in packaged):
                # POL-LUMP: the vendor declined to itemize; the split is never guessed.
                result["decision"], result["escalations"] = "escalate", ["tax_manager"]
            else:
                result["decision"] = "hold"
        elif not all(d["complete"] for d in docs.values()):
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
            payroll_id = next(p["id"] for p in src.data["people"] if p["role"] == "payroll")
            result["requests"].append({"to": payroll_id, "about": "payroll_export"})
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
    handover = handover_drafts(src, new, treatments)
    if any(not h["matches"] for h in handover.values()) and result["decision"] in {"classify_and_book", "post_corrections"}:
        result["decision"] = "hold"          # a colleague's differing draft is resolved before anything is booked
    findings = detect(event, new, attendees, src.rules, src.as_of, classification=classification,
                      corrections=result["corrections"], reassigned=reassigned, gift_missing=missing_gifts,
                      cap_exceeded=cap_exceeded, gift_exceeded=gift_exceeded, emails=src.data["emails"], docs=docs,
                      all_invoices=src.data["invoices"], locked=src.locked, handover=handover,
                      people={p["id"] for p in src.data["people"]})
    findings = list(findings)
    if any(f.kind == "duplicate_invoice" for f in findings):
        # Stage 2 (intake) rejects a duplicate: later stages never run and nothing is created for it.
        findings = [f for f in findings if f.kind in INTAKE_FLAGS]
        result.update({k: ZERO for k in MONEY_FIELDS}, corrections=[], requests=[], escalations=[],
                      payroll_method="none")
        result.pop("per_participant", None)
        payroll, treatments = [], {}
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
    if "lumped_invoice" in result["flags"] and result["decision"] != "escalate":
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
    blocked_by = []
    if blocked and result["decision"] != "no_action":
        blocked_by += ["no visible invoice for this event"] if not invoices else []
        blocked_by += ["invoice arithmetic does not reconcile: " + "; ".join(problems)] if problems else []
        blocked_by += [text for kind, text in BLOCKERS.items() if kind in result["flags"]]
        if classification == "staff_event_nonqualifying" and result["decision"] == "escalate":
            blocked_by.append("POL-BV-FLAT: whether flat tax may be used for a non-qualifying event is the tax "
                              "manager's decision; no payroll method is recorded yet")
        if "lumped_invoice" in result["flags"] and result["decision"] == "escalate":
            blocked_by.append("POL-LUMP: the vendor declined to itemize; escalate, never guess a split")
        blocked_by += [f"{h['drafted_by']}'s unfinished draft {h['entry_id']} for {invoice} differs from the computed "
                       "treatment; the drafter withdraws it or explains it first" for invoice, h in handover.items()
                       if not h["matches"]]
    return plain({"decision": result, "event": event, "invoices": invoices, "new_invoices": new,
                  "attendance": attendees, "treatments": treatments, "payroll": payroll if not blocked else [],
                  "findings": [f.to_record() for f in findings], "rules": sorted(rules_used),
                  "policies": sorted(policies), "arithmetic_problems": problems,
                  "facts": event_facts(src, event, invoices, attendees), "blocked_by": blocked_by,
                  "handover": handover})
