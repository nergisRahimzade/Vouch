"""Runs the agent on a fresh copy of the company and scores what happened. Evaluator-only.

For each run: the company is generated from the seed (by the generator's own command, so the
evaluator never imports it), loaded into a new database, closed by the agent, and answered by
scripted colleagues (so the score measures the agent, not the colleagues). The key stays in its
sealed directory beside the world; the agent only ever sees the database."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from vouch.agent.colleagues import ScriptedColleagues, respond
from vouch.agent.policy import ModelPolicy, ScriptedPolicy, api_key_available
from vouch.agent.runner import run_close
from vouch.db import connect
from vouch.db.rebuild import rebuild
from vouch.evaluator import answer_key
from vouch.evaluator import score as scoring

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = ROOT / "results"
SCRIPTED_LABEL = "SCRIPTED playbook (fast-test mode, not the model)"
TARGETS = {"caught": "100%", "false_holds": "under 5%", "evidence_complete": "100%", "money_correct": "100%",
           "never": "0"}


class NoModelAccess(RuntimeError):
    """The real model was asked for but cannot be called."""


@dataclass
class Prepared:
    set_name: str
    db: Path
    key: dict


def prepare(set_name: str, base: Path, seed: int | None = None) -> Prepared:
    """Generate one set's company and load it. Returns the database every run copies, and the key."""
    world, sealed = base / set_name / "world", base / set_name / "sealed"
    command = [sys.executable, "-m", "vouch.world", "build", "--out", str(world)]
    command += ["--seed", str(seed)] if seed is not None else []
    subprocess.run(command, check=True, capture_output=True, text=True, cwd=ROOT)
    db = base / set_name / "prepared.db"
    rebuild(world, db)
    return Prepared(set_name, db, answer_key.load(sealed))


def run_once(prepared: Prepared, policy, work: Path, index: int) -> dict:
    """One run on its own copy of the prepared database."""
    db = work / f"{prepared.set_name}-run-{index}.db"
    shutil.copyfile(prepared.db, db)
    conn = connect(db)
    try:
        close = run_close(conn, policy)
        colleagues = respond(conn, ScriptedColleagues())
        result = scoring.score_run(conn, prepared.key, close["failures"])
    finally:
        conn.close()
    return {"run": index, "run_id": close["run_id"], "cost_usd": close["cost_usd"], "tokens": close["tokens"],
            "cases": close["cases"], "failures": close["failures"], "report_ok": close["report_ok"],
            "colleagues": {"answered": len(colleagues["answered"]), "posted": len(colleagues["posted"]),
                           "released": colleagues["released"], "refused": len(colleagues["refused"])}, **result}


def _share(good: int, total: int) -> dict:
    return {"good": good, "total": total,
            "percent": str((Decimal(good) / total * 100).quantize(Decimal("0.1"))) if total else None}


def summarise(runs: list[dict]) -> dict:
    """The five measures over every run."""
    plant_ids = list(runs[0]["plants"]) if runs else []
    caught = [p for p in plant_ids if all(r["plants"][p]["caught"] for r in runs)]
    complete = [p for p in plant_ids if all(not r["plants"][p]["evidence_missing"] for r in runs)]
    with_amount = [p for p in plant_ids if runs[0]["plants"][p]["amount_ok"] is not None]
    amounts_ok = [p for p in with_amount if all(r["plants"][p]["amount_ok"] for r in runs)]
    balanced = all(not r["unbalanced_entries"] for r in runs)
    worst = max(runs, key=lambda r: Decimal(r["false_holds"]["rate"]))["false_holds"] if runs else None
    forbidden = sum(r["never"]["count"] for r in runs)
    return {
        "caught": {**_share(len(caught), len(plant_ids)), "missed": [p for p in plant_ids if p not in caught]},
        "false_holds": {"good": len(worst["wrongly_held_or_questioned"]) if worst else 0,
                        "total": worst["clean_subjects"] if worst else 0, "percent": worst["rate"] if worst else None,
                        "worst_run": True},
        "evidence_complete": {**_share(len(complete), len(plant_ids)),
                              "incomplete": [p for p in plant_ids if p not in complete]},
        "money_correct": {**_share(len(amounts_ok), len(with_amount)), "books_balance": balanced,
                          "wrong": [p for p in with_amount if p not in amounts_ok]},
        "never": {"count": forbidden},
    }


def at_target(metrics: dict) -> dict:
    full = lambda m: m["total"] == 0 or m["good"] == m["total"]
    return {"caught": full(metrics["caught"]),
            "false_holds": Decimal(metrics["false_holds"]["percent"] or "0") < 5,
            "evidence_complete": full(metrics["evidence_complete"]),
            "money_correct": full(metrics["money_correct"]) and metrics["money_correct"]["books_balance"],
            "never": metrics["never"]["count"] == 0}


def policy_factory(mode: str, effort: str = "medium"):
    if mode == "scripted":
        return ScriptedPolicy
    if mode != "model":
        raise ValueError(f"unknown mode {mode!r}")
    if not api_key_available():
        raise NoModelAccess("the score command runs the real model by default, and no ANTHROPIC_API_KEY is set "
                            "(environment or .env). Set one, or pass --scripted for the labelled fast-test mode.")
    return lambda: ModelPolicy(effort=effort)


def score_set(set_name: str, runs: int, mode: str, base: Path, seed: int | None = None, factory=None,
              effort: str = "medium") -> dict:
    """Score one set over `runs` runs and return its report."""
    factory = factory or policy_factory(mode, effort)
    prepared = prepare(set_name, base, seed)
    results, model = [], None
    for index in range(1, runs + 1):
        policy = factory()
        model = policy.model
        results.append(run_once(prepared, policy, base, index))
    return make_report(set_name, mode, model, prepared.key, results)


def make_report(set_name: str, mode: str, model: str | None, key: dict, results: list[dict]) -> dict:
    """A set's report from its runs, labelled with how it was produced."""
    metrics = summarise(results)
    cost = sum((Decimal(r["cost_usd"]) for r in results), Decimal("0"))
    return {
        "set": set_name, "mode": mode, "model": model,
        "label": SCRIPTED_LABEL if mode == "scripted" else f"live model {model}",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs": len(results), "seed": key["seed"], "open_month": key["open_month"],
        "cost_usd": str(cost.quantize(Decimal("0.0001"))),
        "tokens": {k: sum(r["tokens"][k] for r in results) for k in ("input", "output", "cache_read")},
        "colleagues": "scripted", "plants": len(key["plants"]),
        "metrics": metrics, "targets": TARGETS, "at_target": at_target(metrics), "per_run": results,
    }


def write(report: dict, results_dir: Path = DEFAULT_RESULTS) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["scored_at"].replace("-", "").replace(":", "").replace("+0000", "Z")
    path = results_dir / f"{report['set']}-{report['mode']}-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def score(set_name: str = "main", runs: int = 1, mode: str = "model", seed: int | None = None,
          results_dir: Path | None = DEFAULT_RESULTS, factory=None, effort: str = "medium") -> dict:
    """Score a set on fresh copies of the company and save the report (unless results_dir is None)."""
    with tempfile.TemporaryDirectory(prefix="vouch-score-") as work:
        report = score_set(set_name, runs, mode, Path(work), seed, factory, effort)
    if results_dir is not None:
        report["report_file"] = write(report, results_dir).name
    return report


def render(report: dict) -> str:
    """The report as plain text: every score labelled with how it was produced."""
    m, ok = report["metrics"], report["at_target"]
    mark = lambda name: "at target" if ok[name] else "BELOW TARGET"
    pct = lambda x: f"{x['good']}/{x['total']}" + (f"  {x['percent']}%" if x["percent"] is not None else "")
    lines = [f"Vouch score: {report['set']} set, {report['label']}, {report['date']}, {report['runs']} run(s), "
             f"cost ${report['cost_usd']} (seed {report['seed']}, colleagues {report['colleagues']})",
             f"  Caught             {pct(m['caught']):<16} target {TARGETS['caught']:<9} {mark('caught')}",
             f"  False holds        {pct(m['false_holds']):<16} target {TARGETS['false_holds']:<9} {mark('false_holds')}",
             f"  Evidence complete  {pct(m['evidence_complete']):<16} target {TARGETS['evidence_complete']:<9} "
             f"{mark('evidence_complete')}",
             f"  Money correct      {pct(m['money_correct']):<16} target {TARGETS['money_correct']:<9} "
             f"{mark('money_correct')}; books balance: {'yes' if m['money_correct']['books_balance'] else 'NO'}",
             f"  Never              {m['never']['count']:<16} target {TARGETS['never']:<9} {mark('never')}"]
    last = report["per_run"][-1] if report["per_run"] else {"plants": {}}
    for plant_id in m["caught"]["missed"]:
        lines.append(f"  missed {plant_id}: {'; '.join(last['plants'][plant_id]['missing']) or 'not caught in every run'}")
    for plant_id in m["evidence_complete"]["incomplete"]:
        lines.append(f"  evidence {plant_id}: {'; '.join(last['plants'][plant_id]['evidence_missing'])}")
    for plant_id in m["money_correct"]["wrong"]:
        lines.append(f"  money {plant_id}: {'; '.join(last['plants'][plant_id]['amount_problems'])}")
    return "\n".join(lines)
