"""Long-running dashboard actions (rebuild, close, colleagues, follow-up, score) run one at a time
in a background thread. Each writes to the database through the same functions the CLI uses, so
the dashboard sees their progress by reading the audit trail while they run."""
from __future__ import annotations

import subprocess
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Busy(RuntimeError):
    """Another job is still running."""


class Jobs:
    def __init__(self):
        self._lock = threading.Lock()
        self.current: dict | None = None
        self.history: list[dict] = []

    @property
    def running(self) -> bool:
        return self.current is not None and self.current["status"] == "running"

    @property
    def rebuilding(self) -> bool:
        return self.running and self.current["kind"] == "rebuild"

    def start(self, kind: str, label: str, work) -> dict:
        """Start `work(log)` in the background. `log(text)` adds a line the dashboard shows."""
        with self._lock:
            if self.running:
                raise Busy(f"{self.current['label']} is still running")
            job = {"id": len(self.history) + 1, "kind": kind, "label": label, "status": "running",
                   "started_at": _now(), "finished_at": None, "log": [], "result": None, "error": None}
            self.current = job
            self.history.append(job)

        def log(text: str) -> None:
            job["log"].append({"at": _now(), "text": text})

        def run() -> None:
            try:
                job["result"] = work(log)
                job["status"] = "done"
            except Exception as error:          # reported to the dashboard as a failed job, with its trace
                job["status"] = "failed"
                job["error"] = f"{type(error).__name__}: {error}"
                log(traceback.format_exc())
            finally:
                job["finished_at"] = _now()

        threading.Thread(target=run, name=f"vouch-job-{job['id']}", daemon=True).start()
        return job

    def snapshot(self) -> dict:
        job = self.current
        return {"running": self.running, "job": None if job is None else {**job, "log": job["log"][-200:]}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_world(variant: str, world: Path, log) -> dict:
    """Generate one of the evaluator's sets with the generator's own command. The dashboard never
    imports the generator, and only the evaluator knows how each set is built."""
    from vouch.evaluator.run import build_command
    log(f"generating the {variant} set from the fixed seed")
    done = subprocess.run(build_command(variant, world), capture_output=True, text=True, cwd=ROOT)
    for line in (done.stdout + done.stderr).strip().splitlines():
        log(line)
    if done.returncode != 0:
        raise RuntimeError(f"the generator failed (exit {done.returncode})")
    return {"variant": variant}
