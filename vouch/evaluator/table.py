"""Measured results only; no handwritten performance or accuracy claims."""
import json
from pathlib import Path
from vouch.evaluator.run import DEFAULT_RESULTS


def load(directory=DEFAULT_RESULTS):
    return [json.loads(p.read_text(encoding="utf-8")) for p in Path(directory).glob("public-*.json")]


def render(reports):
    if not reports:
        return "No measured reports."
    lines=["| Set | Mode | Model | Date | Runs | Passed every run | Safety violations | Cost USD |",
           "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    latest={}
    for r in reports:
        if r.get("status")=="scored" and r.get("runs",0)>=3:
            key=(r["set"],r["mode"])
            if key not in latest or r["date"]>latest[key]["date"]:latest[key]=r
    for r in latest.values():
        lines.append(f"| {r['set']} | {r['mode'].upper()} | {r['model']} | {r['date']} | {r['runs']} | "
                     f"{r['passed_all_runs']}/{r['cases']} | {r['safety_violations']} | {r['cost_usd']} |")
    return "\n".join(lines)
