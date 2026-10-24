import csv
import json

import pytest

from vouch.world.build import build


class WorldFiles:
    def __init__(self, root):
        self.root = root

    def csv(self, path):
        with (self.root / path).open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def json(self, path):
        return json.loads((self.root / path).read_text(encoding="utf-8"))

    def months(self):
        return sorted(p.name for p in (self.root / "months").iterdir())


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    build(root)
    return WorldFiles(root)
