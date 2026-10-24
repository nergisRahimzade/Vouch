"""K6: a month with no plants produces zero cases (noise is explained, never escalated);
with plants, every plant is caught and nothing else is held. Checked on several seeds."""
import pytest

from vouch.db import connect
from vouch.db.rebuild import rebuild
from vouch.evaluator import answer_key
from vouch.evaluator.match import match
from vouch.recon.run import reconcile, stored
from vouch.world import config as C
from vouch.world.build import build


def _close(tmp_path, seed, clean):
    build(tmp_path / "world", seed=seed, clean=clean, sealed=tmp_path / "sealed")
    rebuild(tmp_path / "world", tmp_path / "v.db")
    conn = connect(tmp_path / "v.db")
    reconcile(conn)
    findings = stored(conn)
    conn.close()
    return findings, answer_key.load(tmp_path / "sealed")


@pytest.mark.parametrize("seed", [C.SEED, C.SEED + 4, C.SEED + 9])
def test_a_clean_month_produces_zero_cases(tmp_path, seed):
    findings, key = _close(tmp_path, seed, clean=True)
    assert key["plants"] == []
    assert [f for f in findings if f["disposition"] == "case"] == []
    assert any(f["disposition"] == "explained" for f in findings)      # the noise is still there, explained


@pytest.mark.parametrize("seed", [C.SEED + 1, C.SEED + 4, C.SEED + 11])
def test_with_plants_every_plant_is_a_case_and_nothing_else_is(tmp_path, seed):
    findings, key = _close(tmp_path, seed, clean=False)
    found = match(key["plants"], findings)
    assert not [p for p, hits in found.items() if not hits]
    matched = {f["finding_id"] for hits in found.values() for f in hits}
    assert [f["kind"] for f in findings if f["disposition"] == "case" and f["finding_id"] not in matched] == []
