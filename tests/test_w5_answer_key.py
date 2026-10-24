"""W5: a hidden answer key, written outside the world, loaded only by the evaluator."""
import re
from pathlib import Path

import pytest

from vouch.evaluator import answer_key
from vouch.world.build import build
from vouch.world.plants import MAIN_SET

ROOT = Path(__file__).resolve().parents[1]
VERIFICATION = {"copied-from-source", "independent-arithmetic", "engine-output-pending-hand-check"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    base = tmp_path_factory.mktemp("w5")
    build(base / "world", sealed=base / "sealed")
    build(base / "clean_world", clean=True, sealed=base / "clean_sealed")
    return base


def test_key_is_sealed_outside_the_world(built):
    assert (built / "sealed" / "answer_key.json").exists()
    world_files = [p for p in (built / "world").rglob("*") if p.is_file()]
    assert world_files and not any("answer_key" in p.name or "sealed" in p.parts for p in world_files)
    for path in world_files:
        text = path.read_text(encoding="utf-8")
        assert "expected_outcome" not in text and "trusted_source" not in text, path


def test_evaluator_loads_every_plant_and_noise_item(built):
    key = answer_key.load(built / "sealed")
    assert key["set"] == "main"
    assert [p["plant_id"] for p in key["plants"]] == [p for p, _ in MAIN_SET]
    assert len(key["noise"]) == 4 and all(n["expected_outcome"] == "explain_not_flag" for n in key["noise"])
    for plant in key["plants"]:
        for field in ("kind", "subject", "trusted_source", "expected_outcome", "ask", "rules", "expected_amount"):
            assert field in plant, (plant["plant_id"], field)
        amounts = [v for v in plant.values() if isinstance(v, dict) and "verification" in v]
        assert all(a["verification"] in VERIFICATION for a in amounts)


def test_pending_hand_checks_are_listed(built):
    key = answer_key.load(built / "sealed")
    listed = {(p["plant_id"], p["field"]) for p in key["pending_hand_checks"]}
    actual = {(p["plant_id"], f) for p in key["plants"] for f, v in p.items()
              if isinstance(v, dict) and v.get("verification") == "engine-output-pending-hand-check"}
    assert listed == actual


def test_clean_set_key_has_no_plants(built):
    key = answer_key.load(built / "clean_sealed")
    assert key["set"] == "clean" and key["plants"] == [] and len(key["noise"]) == 4


def test_only_the_generator_and_the_evaluator_touch_the_key():
    allowed = {ROOT / "vouch" / "world" / "answer_key.py", ROOT / "vouch" / "world" / "build.py",
               ROOT / "vouch" / "world" / "__main__.py"}
    for path in (ROOT / "vouch").rglob("*.py"):
        if (ROOT / "vouch" / "evaluator") in path.parents or path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"answer_key|sealed", text), path
