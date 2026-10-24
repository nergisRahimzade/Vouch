"""One entry point that builds the whole world from the seed."""
from __future__ import annotations

import random
from pathlib import Path

from vouch.world import config as C
from vouch.world import payroll
from vouch.world.documents import Bundle, standing_documents
from vouch.world.files import write_bundle
from vouch.world.simulate import World, simulate

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "world"


def generate(seed: int = C.SEED) -> tuple[World, Bundle]:
    """Build every file in memory. Nothing here reads the disk except reference data."""
    world = simulate(seed)
    rng = random.Random(seed + 1)
    bundle = Bundle(dict(standing_documents(world, rng)))
    payroll.run(world, bundle)
    return world, bundle


def build(root: Path = DEFAULT_ROOT, seed: int = C.SEED) -> dict:
    world, bundle = generate(seed)
    manifest = write_bundle(bundle.files, root)
    return {"root": str(root), "seed": seed, "files": len(manifest), "people": len(world.people),
            "months": bundle.months()}
