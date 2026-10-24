"""Materiality (policy FIN-01): a measurement difference strictly below the threshold for its
currency is explained and logged, not escalated; at or above the threshold it is a case.

The rule applies only to measurement differences, where two sources measure the same amount
differently. Control exceptions (an unknown person, a duplicate, changed bank details, a missing
approval, a self-approval) are cases whatever their amount: they are not differences in measurement."""
from __future__ import annotations

from vouch.money import fmt
from vouch.recon.findings import Findings
from vouch.recon.sources import Sources

MEASUREMENT_KINDS = {
    "fx_translation_differs",
    "payment_differs_from_net_pay",
    "bank_amount_differs_from_payment",
    "payroll_calculation_differs",
    "stock_expense_differs",
    "year_to_date_discontinuity",
}


def apply(findings: Findings, src: Sources) -> None:
    for finding in findings:
        if finding.kind not in MEASUREMENT_KINDS or finding.amount is None or finding.disposition != "case":
            continue
        threshold = src.materiality(finding.currency)
        if "FIN-01" not in finding.rules:
            finding.rules.append("FIN-01")
        if abs(finding.amount) < threshold:
            finding.disposition = "explained"
            finding.proposed_action = "log_explanation"
            finding.explanation = (f"Difference of {fmt(abs(finding.amount))} {finding.currency} is below the "
                                   f"materiality threshold of {fmt(threshold)} {finding.currency} (FIN-01); "
                                   f"explained and logged, not escalated.")
        else:
            finding.explanation = (f"Difference of {fmt(abs(finding.amount))} {finding.currency} is at or above the "
                                   f"materiality threshold of {fmt(threshold)} {finding.currency} (FIN-01).")
