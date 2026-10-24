"""K1: pairwise checks find every planted mistake in the main set."""
from decimal import Decimal

from vouch.evaluator.match import match

PAIRS = {
    "HR vs register": {"hr_vs_register"},
    "register vs payment file": {"register_vs_payments"},
    "payment file vs bank": {"payments_vs_bank"},
    "bank vs ledger": {"bank_vs_ledger"},
    "equity platform vs signed grants and HR": {"equity_vs_grants"},
    "approved salary vs paid salary": {"salary_vs_register"},
}


def test_every_planted_mistake_is_found(recon):
    findings, key = recon
    found = match(key["plants"], findings)
    missed = [plant_id for plant_id, hits in found.items() if not hits]
    assert not missed, f"missed: {missed}"
    assert len(found) == 14


def test_every_pairwise_check_runs_and_finds_its_kind(recon):
    findings, _ = recon
    checks = {f["check"] for f in findings}
    for pair, names in PAIRS.items():
        assert names & checks, pair


def test_found_amounts_match_the_key(recon):
    """Amounts as measured against the answer key. A difference here is reported, never tuned away."""
    findings, key = recon
    found = match(key["plants"], findings)
    differences = {}
    for plant in key["plants"]:
        expected = plant["expected_amount"]
        if not expected:
            continue
        amounts = {Decimal(f["amount"]) for f in found[plant["plant_id"]] if f["amount"] is not None}
        if Decimal(expected["value"]) not in amounts:
            differences[plant["plant_id"]] = (expected["value"], sorted(map(str, amounts)))
    assert not differences, differences
