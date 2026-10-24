"""K4: every finding explains itself — the sources compared, the trusted source and why, the rule, the amount."""
from decimal import Decimal as D

import pytest

from vouch.recon.findings import Evidence, Finding, Findings, IncompleteFinding, TRUST


def test_no_stored_finding_lacks_any_part(recon):
    findings, _ = recon
    assert findings
    for f in findings:
        assert f["summary"], f
        assert len(f["evidence"]) >= 2 and all(e["source"] and e["path"] and e["says"] for e in f["evidence"]), f
        assert f["trusted_source"] and f["trust_reason"], f
        assert f["rules"] and all(r[:4] in ("PAY-", "FIN-") for r in f["rules"]), f
        assert f["amount"] is not None and f["currency"] and f["amount_basis"], f
        if f["disposition"] == "explained":
            assert f["explanation"], f
        else:
            assert f["proposed_action"] and f["ask_role"] and f["ask_person"], f


def test_trusted_sources_follow_the_system_of_record_table(recon):
    findings, _ = recon
    by_fact = {reason: source for source, reason in TRUST.values()}
    for f in findings:
        assert by_fact[f["trust_reason"]] == f["trusted_source"]


def _base(**changes):
    fields = dict(check="t", kind="k", period="2026-01", entity="US", fact="cash", subject={"payment_id": "x"},
                  summary="s", evidence=[Evidence("a", "a", (1,), "says a"), Evidence("b", "b", (1,), "says b")],
                  rules=["FIN-06"], amount=D("1.00"), currency="USD", amount_basis="b", proposed_action="p",
                  ask_role="treasury")
    return Finding(**{**fields, **changes})


@pytest.mark.parametrize("change, reason", [
    (dict(evidence=[Evidence("a", "a", (1,), "says a")]), "fewer than two sources"),
    (dict(evidence=[Evidence("a", "a", (1,), ""), Evidence("b", "b", (1,), "says b")]), "does not say"),
    (dict(fact="unknown"), "no trusted source"),
    (dict(rules=[]), "no rule"),
    (dict(amount=None), "no amount"),
    (dict(summary=" "), "no summary"),
])
def test_an_incomplete_finding_is_refused(change, reason):
    with pytest.raises(IncompleteFinding, match=reason):
        Findings().add(_base(**change))


def test_unknown_rules_and_silent_explanations_are_refused_before_storing():
    found = Findings()
    found.add(_base(rules=["FIN-99"]))
    with pytest.raises(IncompleteFinding, match="unknown rules"):
        found.validate({"FIN-06"})
    quiet = Findings()
    quiet.add(_base()).disposition = "explained"
    with pytest.raises(IncompleteFinding, match="without an explanation"):
        quiet.validate({"FIN-06"})
