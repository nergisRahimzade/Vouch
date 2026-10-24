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
HELDOUT_DIR = ROOT / "heldout"
SETS = ("main", "heldout", "clean")       # the planted mistakes; mistakes never used for tuning; no mistakes
KEY_FILE, HELDOUT_KEY_FILE = "answer_key.json", "heldout_answer_key.json"
SCRIPTED_LABEL = "SCRIPTED playbook (fast-test mode, not the model)"
MIN_RUNS = 3          # a score rests on at least this many runs (E3)
DEMO_RUNS = 5         # the demo scenario must pass this many runs in a row
TARGETS = {"caught": "100%", "false_holds": "under 5%", "evidence_complete": "100%", "money_correct": "100%",
           "never": "0"}


class NoModelAccess(RuntimeError):
    """The real model was asked for but cannot be called."""


@dataclass
class Prepared:
    set_name: str
    db: Path
    key: dict
    key_file: str = KEY_FILE


def unavailable(set_name: str, heldout_dir: Path = HELDOUT_DIR) -> str | None:
    """Why a set cannot be scored yet, or None."""
    if set_name not in SETS:
        raise ValueError(f"unknown set {set_name!r}; choose from {', '.join(SETS)}")
    if set_name == "heldout" and not [p for p in heldout_dir.glob("*.py") if not p.name.startswith("_")]:
        return f"no held-out cases have been written yet ({heldout_dir.name}/ holds no plant modules)"
    return None


def build_command(set_name: str, world: Path, seed: int | None = None, heldout_dir: Path = HELDOUT_DIR) -> list[str]:
    """The generator's own command for one set. The evaluator (and the dashboard's sandbox, through
    this function) run it as a separate process and never import the generator."""
    if set_name not in SETS:
        raise ValueError(f"unknown set {set_name!r}; choose from {', '.join(SETS)}")
    command = [sys.executable, "-m", "vouch.world", "build", "--out", str(world)]
    command += ["--seed", str(seed)] if seed is not None else []
    command += {"clean": ["--clean"], "heldout": ["--heldout", str(heldout_dir)]}.get(set_name, [])
    return command


def prepare(set_name: str, base: Path, seed: int | None = None, heldout_dir: Path = HELDOUT_DIR) -> Prepared:
    """Generate one set's company and load it. Returns the database every run copies, and the key.
    The held-out set is scored against its separately written key when there is one."""
    world, sealed = base / set_name / "world", base / set_name / "sealed"
    subprocess.run(build_command(set_name, world, seed, heldout_dir), check=True, capture_output=True, text=True,
                   cwd=ROOT)
    db = base / set_name / "prepared.db"
    rebuild(world, db)
    key = answer_key.load(sealed, KEY_FILE)
    if set_name == "heldout" and (sealed / HELDOUT_KEY_FILE).exists():
        written = answer_key.load(sealed, HELDOUT_KEY_FILE)
        return Prepared(set_name, db, {"seed": key["seed"], "open_month": key["open_month"], **written}, HELDOUT_KEY_FILE)
    return Prepared(set_name, db, key, KEY_FILE)


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
    """The five measures over every run, and how consistent the runs were."""
    return {**measures(runs), "consistency": consistency(runs)}


def measures(runs: list[dict]) -> dict:
    """The five measures. A planted mistake counts as caught, complete or correct only if every run agrees."""
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


def consistency(runs: list[dict]) -> dict:
    """How often each planted mistake was caught, and whether every run handled it the same way."""
    plants = {}
    for plant_id in (runs[0]["plants"] if runs else {}):
        signatures = {tuple(r["plants"][plant_id]["signature"]) for r in runs}
        plants[plant_id] = {"caught_runs": sum(r["plants"][plant_id]["caught"] for r in runs), "of": len(runs),
                            "same_way": len(signatures) == 1}
    return {"plants": plants,
            "flaky": [p for p, c in plants.items() if 0 < c["caught_runs"] < c["of"]],
            "handled_differently": [p for p, c in plants.items() if not c["same_way"]],
            "runs_at_target": [all(at_target(measures([r])).values()) for r in runs]}


def longest_streak(flags: list[bool]) -> int:
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


def demo_verdict(report: dict) -> dict:
    """The demo scenario (the main set's close) passes when DEMO_RUNS runs in a row are each at target."""
    streak = longest_streak(report["metrics"]["consistency"]["runs_at_target"])
    return {"runs": report["runs"], "longest_in_a_row": streak, "needed": DEMO_RUNS,
            "passed": report["set"] == "main" and streak >= DEMO_RUNS}


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
              effort: str = "medium", heldout_dir: Path = HELDOUT_DIR) -> dict:
    """Score one set over `runs` runs and return its report."""
    reason = unavailable(set_name, heldout_dir)
    if reason:
        return not_scored(set_name, mode, reason)
    factory = factory or policy_factory(mode, effort)
    prepared = prepare(set_name, base, seed, heldout_dir)
    results, model = [], None
    for index in range(1, runs + 1):
        policy = factory()
        model = policy.model
        results.append(run_once(prepared, policy, base, index))
    return make_report(set_name, mode, model, prepared.key, results, prepared.key_file)


def _stamp() -> dict:
    now = datetime.now(timezone.utc)
    return {"date": now.date().isoformat(), "scored_at": now.isoformat(timespec="seconds")}


def not_scored(set_name: str, mode: str, reason: str) -> dict:
    """A set that could not be scored: said plainly, with no numbers."""
    return {"set": set_name, "mode": mode, "model": None, "label": "not run", "status": "not scored",
            "reason": reason, **_stamp(), "runs": 0, "cost_usd": "0.0000"}


def make_report(set_name: str, mode: str, model: str | None, key: dict, results: list[dict],
                key_file: str = KEY_FILE) -> dict:
    """A set's report from its runs, labelled with how it was produced."""
    metrics = summarise(results)
    cost = sum((Decimal(r["cost_usd"]) for r in results), Decimal("0"))
    report = {
        "set": set_name, "mode": mode, "model": model,
        "label": SCRIPTED_LABEL if mode == "scripted" else f"live model {model}", "status": "scored", **_stamp(),
        "runs": len(results), "seed": key["seed"], "open_month": key["open_month"], "key_file": key_file,
        "cost_usd": str(cost.quantize(Decimal("0.0001"))),
        "tokens": {k: sum(r["tokens"][k] for r in results) for k in ("input", "output", "cache_read")},
        "colleagues": "scripted", "plants": len(key["plants"]),
        "cases_opened": [r["cases_opened"] for r in results],
        "metrics": metrics, "targets": TARGETS, "at_target": at_target(metrics), "per_run": results,
    }
    return {**report, "demo": demo_verdict(report)}


def write(report: dict, results_dir: Path = DEFAULT_RESULTS) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["scored_at"].replace("-", "").replace(":", "").replace("+0000", "Z")
    path = results_dir / f"{report['set']}-{report['mode']}-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def score(set_name: str = "main", runs: int = 1, mode: str = "model", seed: int | None = None,
          results_dir: Path | None = DEFAULT_RESULTS, factory=None, effort: str = "medium",
          heldout_dir: Path = HELDOUT_DIR) -> dict:
    """Score a set on fresh copies of the company and save the report (unless results_dir is None)."""
    with tempfile.TemporaryDirectory(prefix="vouch-score-") as work:
        report = score_set(set_name, runs, mode, Path(work), seed, factory, effort, heldout_dir)
    if results_dir is not None:
        report["report_file"] = write(report, results_dir).name
    return report


def render(report: dict) -> str:
    """The report as plain text: every score labelled with how it was produced."""
    if report["status"] != "scored":
        return f"Vouch score: {report['set']} set, {report['date']}: NOT SCORED: {report['reason']}"
    m, ok = report["metrics"], report["at_target"]

    def pct(x: dict, planted: bool = True) -> str:
        if planted and x["total"] == 0:
            return "n/a (none planted)"
        return f"{x['good']}/{x['total']}" + (f"  {x['percent']}%" if x["percent"] is not None else "")

    def row(title: str, name: str, value: str, extra: str = "") -> str:
        mark = "at target" if ok[name] else "BELOW TARGET"
        return f"  {title:<19}{value:<19}target {TARGETS[name]:<10}{mark}{extra}"

    books = "; books balance: " + ("yes" if m["money_correct"]["books_balance"] else "NO")
    lines = [f"Vouch score: {report['set']} set, {report['label']}, {report['date']}, {report['runs']} run(s), "
             f"cost ${report['cost_usd']} (seed {report['seed']}, colleagues {report['colleagues']})",
             row("Caught", "caught", pct(m["caught"]), "  (only when every run agrees)"),
             row("False holds", "false_holds", pct(m["false_holds"], planted=False), "  (worst run)"),
             row("Evidence complete", "evidence_complete", pct(m["evidence_complete"])),
             row("Money correct", "money_correct", pct(m["money_correct"]), books),
             row("Never", "never", str(m["never"]["count"]))]
    if report["runs"] < MIN_RUNS:
        lines.append(f"  NOTE: {report['runs']} run(s) only; a score rests on at least {MIN_RUNS} runs")
    opened = report["cases_opened"]
    each = lambda c: f"{c['reconciliation']} from reconciliation, {c['by_agent']} by the agent"
    lines.append("  Cases opened       " + (f"every run: {each(opened[0])}" if all(c == opened[0] for c in opened)
                                          else "; ".join(f"run {n}: {each(c)}" for n, c in enumerate(opened, 1)))
                 + (" (a clean month should have none from reconciliation)" if report["plants"] == 0 else ""))
    c = m["consistency"]
    lines.append(f"  Consistency        runs at target: {sum(c['runs_at_target'])}/{report['runs']}; flaky: "
                 + (", ".join(f"{p} ({c['plants'][p]['caught_runs']}/{c['plants'][p]['of']})" for p in c["flaky"])
                    or "none")
                 + "; handled differently: " + (", ".join(c["handled_differently"]) or "none"))
    if report["set"] == "main":
        d = report["demo"]
        lines.append(f"  Demo scenario      {'PASSED' if d['passed'] else 'NOT PASSED'}: {d['longest_in_a_row']} "
                     f"run(s) in a row at target (needs {d['needed']})")
    last = report["per_run"][-1] if report["per_run"] else {"plants": {}}
    for plant_id in m["caught"]["missed"]:
        lines.append(f"  missed {plant_id}: {'; '.join(last['plants'][plant_id]['missing']) or 'not caught in every run'}")
    for plant_id in m["evidence_complete"]["incomplete"]:
        lines.append(f"  evidence {plant_id}: {'; '.join(last['plants'][plant_id]['evidence_missing']) or 'not complete in every run'}")
    for plant_id in m["money_correct"]["wrong"]:
        lines.append(f"  money {plant_id}: {'; '.join(last['plants'][plant_id]['amount_problems']) or 'not correct in every run'}")
    return "\n".join(lines)
