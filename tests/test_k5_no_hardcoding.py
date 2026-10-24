"""K5: no check, calculation or ledger code names a specific employee, grant, payment, month,
person or planted case. The generator (vouch/world) is the only code allowed to name its own data."""
import re
from pathlib import Path

import pytest

from vouch.world import config as C

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ["vouch/recon", "vouch/calc", "vouch/db", "vouch/evaluator", "vouch/dates.py", "vouch/money.py"]

SPECIFIC = {
    "employee id": r"\bNL-\d{3,}",
    "grant id": r"\bG-\d{3,}",
    "payment id": r"\bPAY-\d{4}-\d{2}-[A-Z]{2}-\d+",
    "bank line id": r"\bBNK-[A-Z]{2}-\d{4}",
    "email id": r"\bEM-\d{3,}",
    "salary change id": r"\bSC-\d{3,}",
    "approval request id": r"\bAR-\d{3,}",
    "ruling id": r"\bRUL-\d{3,}",
    "board resolution": r"\bBR-\d{4}",
    "batch id": r"\bBATCH-\d{4}",
    "planted case": r"\b(MAIN|NOISE|HELD)-\d{2}\b",
    "calendar month": r"\b20\d\d-(0[1-9]|1[0-2])\b",
}


def files():
    for entry in SCANNED:
        path = ROOT / entry
        yield from ([path] if path.is_file() else path.rglob("*.py"))
        if path.is_dir():
            yield from path.rglob("*.sql")


def test_the_patterns_catch_what_they_should():
    sample = "NL-00042 G-00271 PAY-2026-09-US-0121 BNK-DE-2026-03-0001 EM-00041 SC-01194 AR-0013 RUL-0001 " \
             "BR-2026-08 BATCH-2026-08-UK MAIN-04 2026-09"
    assert all(re.search(p, sample) for p in SPECIFIC.values())


@pytest.mark.parametrize("name, pattern", SPECIFIC.items())
def test_no_code_names_a_specific(name, pattern):
    hits = [(str(p.relative_to(ROOT)), m.group()) for p in files()
            for m in re.finditer(pattern, p.read_text(encoding="utf-8"))]
    assert not hits, f"{name}: {hits}"


def test_no_code_names_a_colleague_or_employee():
    names = [d["name"] for d in C.DIRECTORY]
    for path in files():
        text = path.read_text(encoding="utf-8")
        assert not [n for n in names if n in text], path
