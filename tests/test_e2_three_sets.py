"""E2: three sets, each printing its own score: main (the planted mistakes), heldout (mistakes never
used for tuning) and clean (no mistakes). The held-out fixture below is a mechanical stand-in used
only to exercise scoring; it is not, and does not guess at, any held-out case."""
import json
import textwrap

from vouch.agent.policy import ScriptedPolicy
from vouch.evaluator import run
from vouch.evaluator.__main__ import main

FIXTURE = '''
from decimal import Decimal
from vouch.world.plants import OPEN, Plant

def bump_first_payment(plant_id, planter):
    plant = Plant(plant_id, "fixture_bump", OPEN)

    @plant.on(OPEN, "payments")
    def _(ctx):
        row = ctx.rows("payment_file_US.csv")[0]
        row["amount"] = f"{Decimal(row['amount']) + 1:.2f}"
        plant.key.update(plant_id=plant_id, payment_id=row["payment_id"])

    return plant

PLANTS = [("FIXTURE-01", bump_first_payment)]
'''


def fixture_dir(tmp_path, with_key=True):
    held = tmp_path / "held"
    held.mkdir()
    (held / "case.py").write_text(textwrap.dedent(FIXTURE), encoding="utf-8")
    if with_key:                          # written separately from the plant, as the held-out key is
        (held / "answer_key.json").write_text(json.dumps({"plants": [{
            "plant_id": "FIXTURE-01", "kind": "fixture_bump", "month": "open", "subject": {"payment_id": "first"},
            "expected_outcome": "hold_payment", "rules": ["FIN-08"], "precedents": [], "sources_disagree": [],
            "trusted_source": "payroll_register", "ask": {"role": "treasury", "person": "treasury"},
            "expected_amount": None}]}), encoding="utf-8")
    return held


def test_the_clean_set_prints_its_own_score(tmp_path):
    report = run.score_set("clean", 1, "scripted", tmp_path, factory=ScriptedPolicy)
    assert report["set"] == "clean" and report["status"] == "scored" and report["plants"] == 0
    assert report["cases_opened"] == [{"reconciliation": 0, "by_agent": 0}]
    assert report["metrics"]["false_holds"]["good"] == 0 and report["metrics"]["never"]["count"] == 0
    assert all(report["at_target"].values())
    text = run.render(report)
    assert text.startswith("Vouch score: clean set") and "n/a (none planted)" in text


def test_a_held_out_set_with_no_cases_says_so_and_shows_no_numbers(tmp_path):
    empty = tmp_path / "held"
    empty.mkdir()
    (empty / "README.md").write_text("only mechanics here", encoding="utf-8")
    report = run.score("heldout", 1, "scripted", results_dir=tmp_path / "results", heldout_dir=empty)
    assert report["status"] == "not scored" and "no held-out cases" in report["reason"]
    assert "metrics" not in report and report["runs"] == 0
    assert "NOT SCORED" in run.render(report)
    assert json.loads((tmp_path / "results" / report["report_file"]).read_text(encoding="utf-8"))["status"] == "not scored"


def test_the_held_out_set_is_scored_against_its_separately_written_key(tmp_path):
    held = fixture_dir(tmp_path)
    report = run.score_set("heldout", 1, "scripted", tmp_path / "work", factory=ScriptedPolicy, heldout_dir=held)
    assert report["status"] == "scored" and report["key_file"] == run.HELDOUT_KEY_FILE and report["plants"] == 1
    assert report["seed"] and report["open_month"]                  # taken from the build when the key omits them
    scored = report["per_run"][0]["plants"]["FIXTURE-01"]
    assert scored["caught"] is False and scored["missing"] == ["no case was opened"]


def test_without_a_separate_key_the_plants_own_record_is_used(tmp_path):
    held = fixture_dir(tmp_path, with_key=False)
    prepared = run.prepare("heldout", tmp_path / "work", heldout_dir=held)
    assert prepared.key_file == run.KEY_FILE and [p["plant_id"] for p in prepared.key["plants"]] == ["FIXTURE-01"]


def test_one_command_prints_a_score_for_each_set(tmp_path, capsys):
    main(["score", "--scripted", "--results", str(tmp_path)])
    out = capsys.readouterr().out
    headers = [line for line in out.splitlines() if line.startswith("Vouch score:")]
    assert [h.split(",")[0] for h in headers] == ["Vouch score: main set", "Vouch score: heldout set",
                                                 "Vouch score: clean set"]
    assert sorted(p.name.split("-")[0] for p in tmp_path.glob("*.json")) == ["clean", "heldout", "main"]
