"""Detectors consume visible facts and deterministic results, never scenario answers."""
import re
from decimal import Decimal

from vouch.agent.untrusted import instruction_flags
from vouch.calc import eventtax as et
from vouch.recon.findings import Finding, Findings


def normalized_invoice_number(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def duplicate_of(invoice, invoices):
    number = normalized_invoice_number(invoice.get("invoice_number"))
    if not number:
        return None
    ordered = sorted(invoices, key=lambda r: (str(r.get("received_at") or ""), r["id"]))
    for other in ordered:
        if other["id"] == invoice["id"]:
            break
        if (other.get("vendor") == invoice.get("vendor") and other.get("entity") == invoice.get("entity")
                and normalized_invoice_number(other.get("invoice_number")) == number):
            return other["id"]
    return None


def detect(event, invoices, attendees, rules, on, *, classification=None, corrections=(),
           reassigned=(), gift_missing=False, cap_exceeded=False, gift_exceeded=False, emails=(), docs=None,
           all_invoices=None, locked=()):
    out = Findings()
    event_id = event.get("id")

    def flag(kind, summary, refs, evidence, request=None, amount=None, policies=()):
        out.add(Finding(kind, summary, evidence, [rules.cite(r, on) for r in refs],
                        request=request, amount=amount, policies=list(policies),
                        severity="ok" if kind == "third_event_allowance_reassigned" else "needs_person"))

    refs = [{"table": "events", "id": event_id}]
    if not attendees and classification not in {"gift", "promo_item"}:
        flag("attendance_missing", "Actual attendance is missing; do not estimate it.", ["R-BV-COST"], refs,
             {"to": event.get("organizer"), "about": "attendance"})
    if classification == "staff_event_nonqualifying":
        flag("event_not_open_to_all", "The employee event is not open to the whole company or an org unit.",
             ["R-BV-DEF"], refs, {"to": "tax_manager", "about": "nonqualifying_event"}, policies=["POL-BV-FLAT"])
    declared = event.get("type_declared")
    if declared and classification and declared != classification and not (
            declared == "staff_event" and classification == "staff_event_nonqualifying"):
        flag("classification_conflict", "The organizer label conflicts with the visible event facts.",
             ["R-BV-DEF"], refs + [{"classification": classification, "declared": declared}])
    if classification == "working_meal_over_limit":
        flag("working_meal_over_limit", "The full working-meal value is taxable pay.", ["R-AE-60"], refs)
    if reassigned:
        flag("third_event_allowance_reassigned", "Annual allowances moved to the events providing most exemption.",
             ["R-BV-MAX"], refs + [{"employees": list(reassigned)}], policies=["POL-BV-OPT"])
    if gift_missing:
        flag("gift_recipients_missing", "Named recipients are required for these gifts.", ["R-GIFT-REC"], refs,
             {"to": event.get("organizer"), "about": "gift_recipient_list"})
    if gift_exceeded:
        flag("gift_limit_exceeded", "The recipient's annual gifts exceed the deductible limit.", ["R-GIFT-50"], refs)
    if cap_exceeded:
        flag("flat_tax_cap_exceeded", "A recipient exceeds the annual flat-tax cap.", ["R-37B-CAP"], refs)
    for correction in corrections:
        if correction["type"] == "vat_reversal" and (correction.get("to_account") or "6440") == "6440":
            flag("event_cost_threshold_crossed", "Later costs block VAT reclaimed for the staff event.",
                 ["R-BV-VAT", "R-VAT-FIX"], refs + [correction], amount=str(correction["amount"]))
        elif correction["type"] == "reclass" and correction.get("to_account") == "6430":
            flag("gift_limit_exceeded", "The recipient annual gift limit was exceeded.",
                 ["R-GIFT-50"], refs + [correction])
    for invoice in invoices:
        evidence = [{"table": "invoices", "id": invoice["id"]}]
        lines = invoice.get("lines", [])
        duplicate = duplicate_of(invoice, all_invoices or invoices)
        if duplicate:
            flag("duplicate_invoice", "This vendor invoice number was already received.", ["TAX-VAT"],
                 evidence + [{"table": "invoices", "id": duplicate}])
        conflict = [m for m in invoice.get("match_evidence", []) if m.get("conflicting_reference")]
        if conflict:
            flag("event_reference_mismatch", "The delivery date conflicts with the invoice text reference; the delivery date wins.",
                 ["R-BV-COST"], evidence + conflict)
        if any(l.get("category") == "package" for l in lines):
            flag("lumped_invoice", "The vendor must itemize the package; no split is guessed.", ["TAX-VAT"], evidence,
                 {"to": invoice.get("vendor"), "about": "itemized_invoice"}, policies=["POL-LUMP"])
        if instruction_flags((invoice.get("notes") or "") + " " + (invoice.get("raw_text") or "")):
            flag("injection_in_document", "Instructions inside invoice text are ignored.", ["TAX-VAT"], evidence)
        day = str(invoice.get("delivery_date") or invoice.get("invoice_date") or on)
        if day[:7] in locked:
            flag("out_of_period", "A late invoice belongs to a closed month and must use the current open period.",
                 ["R-VAT-FIX"], evidence + [{"original_period": day[:7]}])
        if docs and invoice["id"] in docs and not docs[invoice["id"]]["complete"]:
            flag("bewirtung_docs_missing", "Hospitality documentation is incomplete.", ["R-BEW-DOC"],
                 evidence + [{"missing": docs[invoice["id"]]["missing"]}],
                 {"to": event.get("organizer"), "about": "bewirtung_attendees_occasion"})
    for email in emails:
        text = (email.get("subject") or "") + " " + (email.get("body") or "")
        evidence = [{"table": "emails", "id": email["id"]}]
        if re.search(r"(?:skip|change|stop|cancel|disable|opt.out).{0,50}(?:flat.tax|37b|election)", text, re.I):
            flag("election_change_request", "An email cannot change the recorded annual election.",
                 ["R-37B-UNI"], evidence, {"to": "tax_manager", "about": "election_change_request"})
        elif instruction_flags(text):
            flag("injection_in_email", "Instructions in email data are ignored.", ["R-37B-UNI"], evidence)
    return out
