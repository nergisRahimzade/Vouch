"""Held-out set mechanics: load held-out plants from their own directory and apply them to
the open month on demand. The cases themselves are written separately, in their own
session; this module only knows the interface, never their content.

A held-out directory holds Python modules (any name not starting with '_'). Each defines
    PLANTS = [(plant_id, factory), ...]   factory(plant_id, planter) -> Plant, as in the main set
and may define
    CASTINGS = [Casting(...), ...]        people the cases need, hired like anyone else
The held-out key, if the directory has one, is handled by the build and the key writer, not here.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

from vouch.world import config as C
from vouch.world.plants import MAIN_SET, Plant, Planter
from vouch.world.simulate import Casting

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "heldout"


class HeldOutError(ValueError):
    """A held-out module does not follow the interface, or touches anything but the open month."""


@dataclass
class HeldOut:
    directory: Path
    factories: list[tuple[str, object]] = field(default_factory=list)
    castings: list[Casting] = field(default_factory=list)


def load(directory: Path = DEFAULT_DIR) -> HeldOut:
    if not directory.is_dir():
        raise HeldOutError(f"no held-out directory at {directory}")
    heldout = HeldOut(directory)
    reserved = {plant_id for plant_id, _ in MAIN_SET}
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"vouch_heldout_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        plants = getattr(module, "PLANTS", None)
        if not isinstance(plants, list) or not plants:
            raise HeldOutError(f"{path.name} must define a non-empty PLANTS list")
        for plant_id, factory in plants:
            if plant_id in reserved or not callable(factory):
                raise HeldOutError(f"{path.name}: plant id {plant_id!r} is taken or its factory is not callable")
            reserved.add(plant_id)
            heldout.factories.append((plant_id, factory))
        castings = getattr(module, "CASTINGS", [])
        if not all(isinstance(c, Casting) for c in castings):
            raise HeldOutError(f"{path.name}: CASTINGS must contain Casting objects")
        heldout.castings.extend(castings)
    if not heldout.factories:
        raise HeldOutError(f"no held-out plants in {directory}")
    return heldout


def plants(heldout: HeldOut, planter: Planter) -> list[Plant]:
    """Instantiate the held-out plants; each may act on the open month only."""
    made = []
    for plant_id, factory in heldout.factories:
        plant = factory(plant_id, planter)
        if not isinstance(plant, Plant):
            raise HeldOutError(f"{plant_id}: factory must return a Plant")
        months = {month for month, _, _ in plant.hooks}
        if months != {C.OPEN_MONTH}:
            raise HeldOutError(f"{plant_id}: held-out plants apply to the open month {C.OPEN_MONTH} only, "
                               f"not {sorted(months - {C.OPEN_MONTH}) or 'nothing'}")
        made.append(plant)
    return made
