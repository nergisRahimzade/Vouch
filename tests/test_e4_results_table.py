"""E4: result rendering uses saved reports, with explicit mode/date/run labels."""
from vouch.evaluator import table


def test_table_is_generated_from_reports_and_does_not_invent_scores():
    assert "No measured reports" in table.render([])
    r={"status":"scored","set":"public","mode":"scripted","model":"scripted","date":"2026-10-25",
       "runs":3,"cases":15,"passed_all_runs":14,"cost_usd":"0","safety_violations":0}
    text=table.render([r])
    assert "14/15" in text and "SCRIPTED" in text and "2026-10-25" in text
    assert "100%" not in text
