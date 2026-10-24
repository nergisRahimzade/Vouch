"""K3: differences under the threshold are explained and logged, not escalated."""
from decimal import Decimal as D

from vouch.evaluator.match import match
from vouch.recon import materiality
from vouch.recon.findings import Evidence, Finding, Findings


class _Policy:
    def materiality(self, currency):
        return {"USD": D("50.00"), "GBP": D("40.00"), "EUR": D("45.00")}[currency]


def _finding(kind, amount, currency="EUR"):
    return Finding("test", kind, "2026-01", "DE", "one_payment", {"payment_id": f"P-{kind}-{amount}"}, "difference",
                   [Evidence("a", "a", (1,), "x"), Evidence("b", "b", (1,), "y")], ["FIN-08"], D(amount), currency,
                   "difference", "hold_and_correct", "treasury")


def test_a_tiny_difference_produces_an_explanation_not_a_case():
    found = Findings()
    tiny = found.add(_finding("payment_differs_from_net_pay", "0.01"))
    materiality.apply(found, _Policy())
    assert tiny.disposition == "explained" and "FIN-01" in tiny.rules and "below the materiality" in tiny.explanation


def test_exactly_at_the_threshold_is_a_case_one_cent_below_is_not():
    found = Findings()
    at = found.add(_finding("payment_differs_from_net_pay", "45.00"))
    below = found.add(_finding("payment_differs_from_net_pay", "44.99"))
    negative = found.add(_finding("payment_differs_from_net_pay", "-45.00"))
    materiality.apply(found, _Policy())
    assert (at.disposition, below.disposition, negative.disposition) == ("case", "explained", "case")


def test_control_exceptions_are_cases_whatever_the_amount():
    found = Findings()
    duplicate = found.add(_finding("duplicate_payment", "0.01"))
    materiality.apply(found, _Policy())
    assert duplicate.disposition == "case"


def test_the_planted_fx_difference_is_explained_and_cites_its_precedent(recon):
    findings, key = recon
    fx_noise = next(n for n in key["noise"] if n["kind"] == "fx_difference_within_tolerance")
    explained = match([fx_noise], findings, disposition="explained")[fx_noise["noise_id"]]
    cases = match([fx_noise], findings, disposition="case")[fx_noise["noise_id"]]
    assert explained and not cases
    assert "FIN-01" in explained[0]["rules"] and any(p["ruling_id"] for p in explained[0]["precedents"])


def test_every_noise_item_is_explained_not_escalated(recon):
    findings, key = recon
    for item in key["noise"]:
        if item["kind"] == "email_typos":
            continue                      # typos produce no finding at all
        assert match([item], findings, disposition="explained")[item["noise_id"]], item["noise_id"]
        assert not match([item], findings, disposition="case")[item["noise_id"]], item["noise_id"]
