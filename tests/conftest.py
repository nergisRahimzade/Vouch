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


@pytest.fixture(scope="session")
def db_path(world, tmp_path_factory):
    from vouch.db.rebuild import rebuild
    path = tmp_path_factory.mktemp("db") / "vouch.db"
    rebuild(world.root, path)
    return path


@pytest.fixture
def db(db_path):
    from vouch.db import connect
    conn = connect(db_path)
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def scratch_db(db_path, tmp_path):
    """A private copy of the session database for tests that write."""
    import shutil
    from vouch.db import connect
    copy = tmp_path / "scratch.db"
    shutil.copyfile(db_path, copy)
    conn = connect(copy)
    yield conn
    conn.close()
