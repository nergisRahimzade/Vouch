"""Cast people (hired on purpose for planted cases) cannot be told apart from anyone else,
and the casting list stays inside the generator."""
import re
from collections import defaultdict
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from vouch import dates
from vouch.world import config as C
from vouch.world.build import generate
from vouch.world.plants import castings
from vouch.world.simulate import simulate_with_cast

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def cast_world():
    world, cast = simulate_with_cast(C.SEED, castings())
    return world, set(cast)


def shape(value: str) -> str:
    """Character-class shape of a value, with runs collapsed: 'NL-00042' -> 'A-9', '2026-03-01' -> '9-9-9'."""
    text = re.sub(r"[^\W\d_]", lambda m: "A" if m.group().isupper() else "a", value)
    text = re.sub(r"\d", "9", text)
    return re.sub(r"(.)\1+", r"\1", text)


def test_the_cast_is_present_and_matches_the_generated_world():
    world, cast = cast_world()
    assert len(cast) == len(castings())
    generated = generate(clean=True, noise=False).world
    assert [p.employee_id for p in generated.people] == [p.employee_id for p in world.people]


def test_ids_follow_the_same_numbering():
    world, cast = cast_world()
    ids = [p.employee_id for p in world.people]
    assert all(re.fullmatch(r"NL-\d{5}", i) for i in ids)
    assert ids == [f"NL-{n:05d}" for n in range(1, len(ids) + 1)]
    hire_months = [dates.month_of(p.hire_date) for p in world.people]
    assert hire_months == sorted(hire_months)
    # Cast people are not always the last hire of their month.
    last_of_month = {}
    for person in world.people:
        last_of_month[dates.month_of(person.hire_date)] = person.employee_id
    assert not cast <= set(last_of_month.values())


def test_no_attribute_marks_a_cast_person():
    world, cast = cast_world()
    fields = {tuple(sorted(vars(p))) for p in world.people}
    assert len(fields) == 1
    for person in world.people:
        assert not any("cast" in str(v).lower() or "MAIN-" in str(v) for v in vars(person).values())


def test_names_salaries_and_dates_are_drawn_the_same_way():
    world, cast = cast_world()
    for person in world.people:
        entity = person.stints[0].entity
        assert person.first_name in C.FIRST_NAMES[entity] and person.last_name in C.LAST_NAMES[entity]
        hire = person.salaries[0]
        base = Decimal(C.US_ENGINEERING_BASE[hire.level]) * C.ENTITY_SALARY_FACTOR[entity] \
            * C.DEPARTMENT_SALARY_FACTOR[person.department]
        assert base * Decimal("0.9") - 500 <= hire.annual_salary <= base * Decimal("1.1") + 500
        assert hire.reason == "hire" and hire.effective_date == person.hire_date
    cast_days = {p.hire_date.day == 1 for p in world.people if p.employee_id in cast}
    assert cast_days <= {True, False}


def test_every_field_in_every_file_looks_the_same_for_cast_and_others():
    _, cast = cast_world()
    files = generate().bundle.files
    for path, content in files.items():
        if not path.endswith(".csv") or not content or "employee_id" not in content[0]:
            continue
        normal, casted = defaultdict(set), defaultdict(set)
        for row in content:
            target = casted if row["employee_id"] in cast else normal
            for field, value in row.items():
                target[field].add(shape(value))
        for field, shapes in casted.items():
            assert shapes <= normal[field], (path, field, shapes - normal[field])


def test_no_file_mentions_casting_or_plants():
    files = generate().bundle.files
    for path, content in files.items():
        text = str(content)
        assert "MAIN-" not in text and "NOISE-" not in text and "Casting" not in text, path
        assert not re.search(r"\bcast\b", text, re.IGNORECASE), path


def test_only_the_generator_can_import_the_generator():
    allowed = {ROOT / "vouch" / "__main__.py"}
    for path in (ROOT / "vouch").rglob("*.py"):
        if (ROOT / "vouch" / "world") in path.parents or path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+vouch\.world\b", text, re.MULTILINE), path
        assert "castings" not in text, path
