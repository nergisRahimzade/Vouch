import shutil
from pathlib import Path

import pytest

from vouch.world.scenario import parse

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "cases" / "public"


@pytest.fixture(scope="session")
def base_db_path(tmp_path_factory):
    """The base company on its fixtures' as_of date."""
    from vouch.db.rebuild import rebuild
    path = tmp_path_factory.mktemp("base") / "vouch.db"
    rebuild(path)
    return path


@pytest.fixture
def db(base_db_path, tmp_path):
    """A private copy of the base company database."""
    from vouch.db import connect
    copy = tmp_path / "base.db"
    shutil.copyfile(base_db_path, copy)
    conn = connect(copy)
    yield conn
    conn.close()


@pytest.fixture
def scenario_db(tmp_path):
    """Build a database for a scenario given as a dict (the YAML mapping); returns an open connection."""
    from vouch.db import connect
    from vouch.db.rebuild import rebuild
    opened = []

    def make(raw: dict):
        path = tmp_path / f"{raw['id']}-{len(opened)}.db"
        rebuild(path, parse(raw))
        conn = connect(path)
        opened.append(conn)
        return conn

    yield make
    for conn in opened:
        conn.close()


@pytest.fixture
def public_case(tmp_path):
    """A fresh visible company for a public scenario; expected answers never enter the agent."""
    from vouch.db import connect
    from vouch.db.rebuild import rebuild
    from vouch.world.scenario import load_file
    from vouch.agent.runner import ensure
    opened=[]
    def make(case_id):
        scenario=load_file(PUBLIC / (case_id + ".yaml"))[0]
        path=tmp_path / f"{case_id}-{len(opened)}.db"
        rebuild(path,scenario)
        conn=connect(path)
        ensure(conn)
        opened.append(conn)
        return conn,scenario
    yield make
    for conn in opened:
        conn.close()


@pytest.fixture
def controls_db(db):
    from vouch.agent.runner import ensure
    ensure(db)
    return db
