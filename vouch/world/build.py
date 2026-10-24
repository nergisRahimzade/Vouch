"""One entry point that builds the whole world from the seed."""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from vouch.world import config as C
from vouch.world import payroll
from vouch.world.documents import Bundle, standing_documents
from vouch.world.files import write_bundle
from vouch.world.plants import Plant, Planter, hooks_for, main_set
from vouch.world.simulate import World, simulate

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "world"


@dataclass
class Generated:
    world: World
    bundle: Bundle
    plants: list[Plant]


def generate(seed: int = C.SEED, plants: list[str] | None = None, clean: bool = False) -> Generated:
    """Build every file in memory.

    clean=True    no planted mistakes (the clean set)
    plants=[ids]  only these main-set plants (used by tests to see one plant's effect)
    """
    world = simulate(seed)
    bundle = Bundle(dict(standing_documents(world, random.Random(seed + 1))))
    planter = Planter(seed)
    for document in ("standing/rulings.json",):
        for ruling in bundle.files[document]["rulings"]:
            if ruling["subject_employee_id"]:
                planter.claim(ruling["subject_employee_id"])
    planted = [] if clean else main_set(planter, plants)
    payroll.run(world, bundle, hooks_for(planted))
    return Generated(world, bundle, planted)


def build(root: Path = DEFAULT_ROOT, seed: int = C.SEED, clean: bool = False) -> dict:
    result = generate(seed, clean=clean)
    manifest = write_bundle(result.bundle.files, root)
    return {"root": str(root), "seed": seed, "files": len(manifest), "people": len(result.world.people),
            "months": result.bundle.months(), "plants": len(result.plants)}
