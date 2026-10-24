"""Writes the hidden answer key. It goes to a sealed directory outside the world, so
nothing that loads the world can read it; only vouch.evaluator loads it."""
from __future__ import annotations

import json
from pathlib import Path

from vouch.world import config as C

DEFAULT_SEALED = Path(__file__).resolve().parents[2] / "sealed"
KEY_FILE = "answer_key.json"


def answer_key(set_name: str, seed: int, plants: list, noise: list) -> dict:
    pending = [{"plant_id": p.key["plant_id"], "field": field}
               for p in plants for field, value in p.key.items()
               if isinstance(value, dict) and value.get("verification") == "engine-output-pending-hand-check"]
    return {
        "set": set_name, "seed": seed, "company": C.COMPANY, "open_month": C.OPEN_MONTH,
        "about": "Planted mistakes and noise for one build. Read only by vouch.evaluator. "
                 "Amounts labelled engine-output-pending-hand-check are not yet independently verified.",
        "plants": [p.key for p in plants],
        "noise": [n.key for n in noise],
        "pending_hand_checks": pending,
    }


def write(sealed: Path, key: dict) -> Path:
    sealed.mkdir(parents=True, exist_ok=True)
    path = sealed / KEY_FILE
    path.write_bytes((json.dumps(key, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return path
