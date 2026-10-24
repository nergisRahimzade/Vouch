"""E4: one results table, generated from the reports and shown in the README (and, for the UI, in
results/summary.json); never typed by hand."""
import copy
import json

from vouch.evaluator import run, table


def test_the_readme_table_is_exactly_what_the_reports_generate():
    reports = table.load()
    assert reports, "no reports in results/; run `python -m vouch score`"
    assert table.shown() == table.render(reports)


def test_the_summary_for_the_ui_carries_the_same_rows():
    summary = json.loads((run.DEFAULT_RESULTS / "summary.json").read_text(encoding="utf-8"))
    assert summary["rows"] == [table.row(r) for r in table.latest(table.load())]


def test_every_number_shown_comes_from_its_report():
    for report in table.latest(table.load()):
        shown = table.row(report)
        assert shown["Runs"] == str(report["runs"]) and shown["Cost (USD)"] == report["cost_usd"]
        if report["status"] == "scored":
            caught = report["metrics"]["caught"]
            assert shown["Caught"] == "n/a" if caught["total"] == 0 else str(caught["good"]) in shown["Caught"]
            assert shown["Never"] == str(report["metrics"]["never"]["count"])


def _report(set_name="main", mode="scripted", runs=3, at="2026-01-01T00:00:00+00:00", caught=14):
    report = copy.deepcopy(next(r for r in table.load() if r["status"] == "scored" and r["set"] == "main"))
    report.update(set=set_name, mode=mode, runs=runs, scored_at=at, report_file=f"{set_name}-{mode}-{at}.json",
                  model="claude-opus-5-5" if mode == "model" else None)
    report["metrics"]["caught"]["good"] = caught
    return report


def test_the_newest_report_per_set_and_mode_is_shown_and_short_runs_are_left_out():
    older, newer = _report(at="2026-01-01T00:00:00+00:00", caught=10), _report(at="2026-02-01T00:00:00+00:00")
    short = _report(at="2026-03-01T00:00:00+00:00", runs=1, caught=1)
    chosen = table.latest([older, newer, short])
    assert chosen == [newer]


def test_a_live_report_is_listed_first_and_the_scripted_note_goes_away():
    scripted, live = _report(), _report(mode="model", at="2025-12-01T00:00:00+00:00")
    text = table.render([scripted, live])
    rows = [line for line in text.splitlines() if line.startswith("| main")]
    assert rows[0].startswith("| main | live | claude-opus-5-5") and "No live-model report yet" not in text
    assert "No live-model report yet" in table.render([scripted])


def test_a_hand_edited_table_is_overwritten_on_the_next_update(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "main.json").write_text(json.dumps(_report()), encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(f"intro\n{table.START}\n| typed | by | hand |\n{table.END}\noutro\n", encoding="utf-8")
    assert table.shown(readme) != table.render(table.load(results))
    table.update(results, readme)
    assert table.shown(readme) == table.render(table.load(results))
    assert readme.read_text(encoding="utf-8").startswith("intro\n") and readme.read_text(encoding="utf-8").endswith("outro\n")
