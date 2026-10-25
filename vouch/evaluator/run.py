"""Isolated public/owner-holdout evaluation. Holdout details never leave the scorer by default."""
import asyncio
import json
import sqlite3
from time import perf_counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from vouch.agent.policy import ModelPolicy, ScriptedPolicy
from vouch.agent.runner import run_scenario
from vouch.db import connect
from vouch.db.rebuild import rebuild
from vouch.evaluator.answer_key import load_cases
from vouch.evaluator.score import score_decisions

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = ROOT / "results"
MIN_RUNS, DEMO_RUNS = 3, 5


def unbuildable(scenario, expected, error, scripted, model_options):
    """A case whose world cannot be built is scored as failed (never skipped), before any model call."""
    from vouch.agent.policy import MODEL
    expects = expected if isinstance(expected, list) else [expected]
    message = f"world could not be built: {type(error).__name__}: {error}"
    return {"case_id": scenario.id, "error": message,
            "score": {"passed": False, "amounts_correct": False, "books_balance": True, "safety_violations": [],
                      "tasks": [{"passed": False, "errors": [message], "amounts_correct": False,
                                 "false_positive_flags": [], "safety_violations": []} for _ in expects]},
            "run": {"model": "scripted" if scripted else (model_options or {}).get("model", MODEL), "decisions": [],
                    "metrics": {"wall_seconds": 0.0, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "tool_seconds": 0.0,
                                "cost_usd": "0"}}}


def evaluate_one(scenario, expected, scripted=False, model_options=None):
    from vouch.world.scenario import ScenarioError
    with TemporaryDirectory(prefix="vouch-score-") as temporary:
        path = Path(temporary) / "case.db"
        try:
            rebuild(path, scenario)
        except (ScenarioError, ValueError, KeyError, sqlite3.Error) as error:
            return unbuildable(scenario, expected, error, scripted, model_options)
        with closing(connect(path)) as conn:
            result = run_scenario(conn, scenario, ScriptedPolicy() if scripted else ModelPolicy(**({"effort":"medium", **(model_options or {})})))
            score = score_decisions(conn, result["decisions"], expected)
            return {"case_id": scenario.id, "score": score, "run": result}


async def _batch(cases, scripted, concurrency, checkpoint_dir=None, model_options=None):
    semaphore = asyncio.Semaphore(concurrency)
    async def one(scenario, expected):
        async with semaphore:
            row = await asyncio.to_thread(evaluate_one, scenario, expected, scripted, model_options)
            if checkpoint_dir is not None:
                from uuid import uuid4
                checkpoint_dir.mkdir(parents=True, exist_ok=True)
                (checkpoint_dir / (uuid4().hex + ".json")).write_text(json.dumps(row, indent=2), encoding="utf-8")
            return row
    return await asyncio.gather(*(one(s, e) for s, e in cases))


def evaluate(*, directory=None, scripted=False, runs=3, holdout=False, reveal=False, concurrency=1,
             case_ids=None, results_dir=DEFAULT_RESULTS, save=True, model_options=None):
    if runs < MIN_RUNS:
        raise ValueError("scores require at least three runs")
    if not 1 <= concurrency <= 6:
        raise ValueError("case concurrency must be between one and six")
    now = datetime.now(timezone.utc).isoformat()
    model_options = {"effort":"medium", **(model_options or {})}
    try:
        cases = load_cases(directory, holdout=holdout)
    except FileNotFoundError as error:
        return {"status": "not_scored", "reason": str(error), "set": "holdout" if holdout else "public",
                "mode": "scripted" if scripted else "model", "date": now, "runs": 0}
    if case_ids:
        cases = [(s,e) for s,e in cases if s.id in set(case_ids)]
        if not cases:
            raise ValueError("no selected public scenarios")
    started = perf_counter()
    checkpoint_dir = Path(results_dir) / "checkpoints" if save and not holdout else None
    batches = [asyncio.run(_batch(cases, scripted, concurrency, checkpoint_dir, model_options)) for _ in range(runs)]
    all_rows = [row for batch in batches for row in batch]
    passed = sum(all(batch[i]["score"]["passed"] for batch in batches) for i in range(len(cases)))
    amounts = sum(all(batch[i]["score"]["amounts_correct"] for batch in batches) for i in range(len(cases)))
    violations = sum(len(row["score"]["safety_violations"]) for row in all_rows)
    metrics = [row["run"]["metrics"] for row in all_rows]
    costs = [m["cost_usd"] for m in metrics]
    result = {"status": "scored", "set": "holdout" if holdout else "public", "mode": "scripted" if scripted else "model",
              "model": all_rows[0]["run"]["model"], "date": now, "runs": runs, "model_options": model_options or {},
              "cases": len(cases), "passed_all_runs": passed, "amounts_correct_all_runs": amounts,
              "safety_violations": violations, "passed": passed == len(cases) and violations == 0,
              "seconds_per_case": sum(m["wall_seconds"] for m in metrics) / len(metrics),
              "llm_calls_per_case": sum(m["llm_calls"] for m in metrics) / len(metrics),
              "cost_usd": str(sum((__import__("decimal").Decimal(c) for c in costs), __import__("decimal").Decimal(0))) if all(c is not None for c in costs) else None,
              "suite_wall_seconds": perf_counter() - started,
              "false_positive_flags": max(sum(len(t["false_positive_flags"]) for row in batch for t in row["score"]["tasks"]) for batch in batches)}
    from decimal import Decimal
    from vouch.controls.matrix import INFO_FLAGS
    def anomalous(expected):
        expectations = expected if isinstance(expected, list) else [expected]
        return any((set(e.get("flags", [])) - INFO_FLAGS) or e.get("corrections") or
                   e.get("decision") in {"hold","escalate","post_corrections","reject_duplicate"} or
                   Decimal(str(e.get("wage_taxable", "0"))) != 0 for e in expectations)
    anomaly_indices = [i for i, (_, expected) in enumerate(cases) if anomalous(expected)]
    clean_indices = [i for i in range(len(cases)) if i not in anomaly_indices]
    caught = sum(all(batch[i]["score"]["passed"] for batch in batches) for i in anomaly_indices)
    false_positive_cases = max(sum(any(t["false_positive_flags"] for t in batch[i]["score"]["tasks"]) or
                                    any(d["decision"] in {"hold","escalate"} for d in batch[i]["run"]["decisions"])
                                    for i in clean_indices) for batch in batches)
    def percent(numerator, denominator):
        return float(Decimal(numerator) * 100 / denominator) if denominator else None
    result["catch_rate"] = {"caught":caught,"total":len(anomaly_indices),"percent":percent(caught,len(anomaly_indices))}
    result["false_positive_rate"] = {"cases":false_positive_cases,"total":len(clean_indices),
                                      "percent":percent(false_positive_cases,len(clean_indices))}
    result["amount_accuracy"] = {"correct":amounts,"total":len(cases),"percent":percent(amounts,len(cases))}
    if not holdout or reveal:
        result["details"] = batches
    if save:
        results_dir = Path(results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        name = f"{result['set']}-{result['mode']}-{now.replace(':','').replace('.','')}.json"
        (results_dir / name).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
