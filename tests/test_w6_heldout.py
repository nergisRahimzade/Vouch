"""W6: held-out plant mechanics. The fixture plant below is a mechanical stand-in used only to
exercise the loader; it is not, and does not guess at, any held-out case."""
import json
import re
import textwrap
from pathlib import Path

import pytest

from vouch.world import config as C
from vouch.world.build import build, generate
from vouch.world.heldout import HeldOutError, load

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = '''
from decimal import Decimal
from vouch.world.plants import OPEN, Plant

def bump_first_payment(plant_id, planter):
    plant = Plant(plant_id, "fixture_bump", OPEN)

    @plant.on(OPEN, "payments")
    def _(ctx):
        row = ctx.rows("payment_file_US.csv")[0]
        row["amount"] = f"{Decimal(row['amount']) + 1:.2f}"
        plant.key.update(plant_id=plant_id, payment_id=row["payment_id"])

    return plant

PLANTS = [("FIXTURE-01", bump_first_payment)]
'''

CLOSED_MONTH = '''
from vouch.world.plants import Plant, month

def touches_closed(plant_id, planter):
    plant = Plant(plant_id, "fixture_closed", month(-1))
    plant.on(month(-1), "payments")(lambda ctx: None)
    return plant

PLANTS = [("FIXTURE-02", touches_closed)]
'''


def write(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(textwrap.dedent(text), encoding="utf-8")
    return directory


def test_heldout_plants_apply_to_the_open_month_on_demand(tmp_path):
    held = write(tmp_path / "held", "case.py", FIXTURE)
    (held / "answer_key.json").write_text(json.dumps({"written": "separately"}), encoding="utf-8")
    result = build(tmp_path / "world", heldout_dir=held, sealed=tmp_path / "sealed")
    assert result["set"] == "heldout" and result["plants"] == 1

    planted = generate(heldout=load(held)).bundle.files
    clean = generate(clean=True).bundle.files
    changed = {p for p in clean if clean[p] != planted[p]}
    assert changed == {f"months/{C.OPEN_MONTH}/payment_file_US.csv"}

    key = json.loads((tmp_path / "sealed" / "answer_key.json").read_text(encoding="utf-8"))
    assert key["set"] == "heldout" and [p["plant_id"] for p in key["plants"]] == ["FIXTURE-01"]
    copied = json.loads((tmp_path / "sealed" / "heldout_answer_key.json").read_text(encoding="utf-8"))
    assert copied == {"written": "separately"}


def test_a_heldout_plant_outside_the_open_month_is_refused(tmp_path):
    held = write(tmp_path / "held", "case.py", CLOSED_MONTH)
    with pytest.raises(HeldOutError, match="open month"):
        generate(heldout=load(held))


def test_interface_errors_are_refused(tmp_path):
    with pytest.raises(HeldOutError):
        load(tmp_path / "missing")
    with pytest.raises(HeldOutError, match="PLANTS"):
        load(write(tmp_path / "empty", "case.py", "X = 1\n"))
    with pytest.raises(HeldOutError, match="taken"):
        load(write(tmp_path / "clash", "case.py", FIXTURE.replace("FIXTURE-01", "MAIN-01")))


def test_clean_and_heldout_cannot_be_combined(tmp_path):
    held = write(tmp_path / "held", "case.py", FIXTURE)
    with pytest.raises(ValueError):
        build(tmp_path / "world", clean=True, heldout_dir=held)


def test_nothing_outside_the_generator_and_evaluator_knows_about_the_heldout_set():
    for path in (ROOT / "vouch").rglob("*.py"):
        if (ROOT / "vouch" / "world") in path.parents or (ROOT / "vouch" / "evaluator") in path.parents:
            continue
        assert not re.search(r"heldout|held-out|held_out", path.read_text(encoding="utf-8"), re.IGNORECASE), path
