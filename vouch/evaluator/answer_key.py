"""The single loader for the sealed answer key."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SEALED = Path(__file__).resolve().parents[2] / "sealed"


def load(sealed: Path = DEFAULT_SEALED, name: str = "answer_key.json") -> dict:
    path = sealed / name
    if not path.exists():
        raise FileNotFoundError(f"no answer key at {path}; run `python -m vouch.world build` first")
    return json.loads(path.read_text(encoding="utf-8"))
