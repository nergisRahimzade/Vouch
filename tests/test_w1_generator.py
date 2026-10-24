"""W1: one command builds every month from a fixed seed; two runs are identical."""
import filecmp

from vouch import dates
from vouch.world import config as C
from vouch.world.build import build


def _same_tree(a, b) -> bool:
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    return not mismatch and not errors and all(_same_tree(a / d, b / d) for d in cmp.common_dirs)


def test_two_builds_are_byte_identical(tmp_path):
    first = build(tmp_path / "one")
    second = build(tmp_path / "two")
    assert first["files"] == second["files"] > 0
    assert _same_tree(tmp_path / "one", tmp_path / "two")


def test_a_different_seed_changes_the_world(tmp_path):
    build(tmp_path / "one")
    build(tmp_path / "other", seed=C.SEED + 1)
    assert not _same_tree(tmp_path / "one", tmp_path / "other")


def test_twelve_closed_months_plus_one_open(tmp_path):
    result = build(tmp_path / "w")
    months = result["months"]
    assert months[-1] == C.OPEN_MONTH
    assert len(months) == 13 and months == dates.month_range(C.FIRST_FILE_MONTH, C.OPEN_MONTH)
    for key in months:
        assert (tmp_path / "w" / "months" / key / "hr_census.csv").exists()
