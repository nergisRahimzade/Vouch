"""K5: production calculations, detectors, controls and agent do not name public-case data."""
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_production_logic_contains_no_public_case_or_person_special_cases():
    patterns=[r"\b(?:P(?:0[1-9]|1[01])|C0[1-4])\b",r"\bEVT-(?:SOM26|FAS26|XMAS26|FK26)\b",
              r"\bINV-(?:FW-0910|GIFT-1207|AGT-77)\b",r"\bBP-(?:003|015)\b"]
    hits=[]
    for folder in ("calc","recon","controls","agent"):
        for p in (ROOT/"vouch"/folder).glob("*.py"):
            for pattern in patterns:
                if re.search(pattern,p.read_text(encoding="utf-8")): hits.append(str(p))
    assert not hits


def test_runtime_layers_never_import_answer_key():
    for folder in ("calc","recon","controls","agent","ui"):
        for p in (ROOT/"vouch"/folder).glob("*.py"):
            assert "evaluator.answer_key" not in p.read_text(encoding="utf-8"),p
