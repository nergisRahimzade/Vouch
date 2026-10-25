"""Measured baseline; no extrapolated model savings or unrun holdout claims."""
import json
from datetime import datetime, timezone
from pathlib import Path
from vouch.evaluator.answer_key import load_cases
from vouch.evaluator.run import evaluate_one


def bench(path=Path("perf_baseline.json"), scripted=False, model_options=None):
    rows = [evaluate_one(s, e, scripted, model_options) for s, e in load_cases()]
    result = {"date": datetime.now(timezone.utc).isoformat(), "mode": "scripted" if scripted else "model",
              "runs": 1, "model": rows[0]["run"]["model"], "model_options":{"effort":"medium", **(model_options or {})},
              "cases": [{"id": r["case_id"], "passed": r["score"]["passed"], **r["run"]["metrics"]} for r in rows]}
    Path(path).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
