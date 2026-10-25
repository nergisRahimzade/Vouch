"""G5: amounts, dated rules and identifiers must appear in tool evidence."""
import pytest
from vouch.agent.claims import check


def test_supported_claim_passes():
    text="EVT-TEST applies R-BV-COST and POL-ROUND on 2026-07-03: 107.43."
    assert check(text,[text],[]).ok


@pytest.mark.parametrize("text",["Tax is 999.99","R-UNKNOWN applies","INV-UNKNOWN was paid","Date 2026-01-01"])
def test_unsupported_claim_is_rejected(text):
    assert not check(text,["Known: 5.00 R-BV-COST"],[]).ok
