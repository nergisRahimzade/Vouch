"""Evaluator-only scenario answers. The agent receives parsed Scenarios with expect stripped."""
import os
from pathlib import Path
from vouch.world.scenario import parse, read_yaml

PUBLIC = Path(__file__).resolve().parents[2] / "cases" / "public"


def load_cases(directory=None, *, holdout=False):
    if holdout:
        value = os.environ.get("VOUCH_HOLDOUT_PATH")
        if not value:
            raise FileNotFoundError("VOUCH_HOLDOUT_PATH is not configured; holdout not scored")
        directory = Path(value)
    directory = Path(directory or PUBLIC)
    files = [directory] if directory.is_file() else sorted(directory.glob("*.y*ml"))
    if not files:
        raise FileNotFoundError("no scenarios available for scoring")
    cases = []
    for file in files:
        raw = read_yaml(file)
        for item in raw if isinstance(raw, list) else [raw]:
            expected = item.get("expect")
            if expected is None:
                raise ValueError("scoring scenario has no expect block")
            cases.append((parse(item), expected))
    return cases
