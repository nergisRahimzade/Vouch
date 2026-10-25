"""Background run registry; each run owns its SQLite database and replayable timeline."""
import threading
from pathlib import Path
from uuid import uuid4

from vouch.agent.policy import ScriptedPolicy, ModelPolicy
from vouch.agent.runner import run_scenario
from vouch.db import connect
from vouch.db.rebuild import rebuild


FRIENDLY = ("Vouch paused this run: the AI model is not available right now. Nothing was booked or posted; "
            "the case is on hold and can be run again in a moment.")


class Runs:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.items, self.latest = {}, None
        self.settings = {}
        self.lock = threading.Lock()

    def start(self, scenario, scripted=True, model=None, effort="medium", *, existing=None, run_id=None):
        run_id = run_id or uuid4().hex
        path = existing or self.root / (run_id + ".db")
        job = {"run_id": run_id, "status": "running", "timeline": [], "result": None, "error": None,
               "db": path, "scenario_id": scenario.id}
        with self.lock:
            if existing:
                job["timeline"] = list(self.items[run_id]["timeline"])
            self.items[run_id] = job
            self.settings[run_id] = (scenario, scripted, model, effort)
        def emit(step):
            with self.lock:
                job["timeline"].append(step)
        def work():
            conn = None
            try:
                if existing is None:
                    rebuild(path, scenario)
                conn = connect(path)
                conn.execute("PRAGMA journal_mode=WAL")
                result = run_scenario(conn, scenario, ScriptedPolicy() if scripted else ModelPolicy(model=model,effort=effort), emit)
                with self.lock:
                    job["result"], job["status"] = result, result.get("status", "done")
                    if job["status"] != "done":
                        job["error"] = FRIENDLY
                    self.latest = path
            except Exception as error:
                # Report a failed job; never manufacture a successful result after an exception.
                import logging
                logging.getLogger("vouch").exception("run %s failed", run_id)   # details stay in the server log
                with self.lock:
                    job["error"], job["status"] = FRIENDLY, "failed"
            finally:
                if conn is not None:
                    conn.close()
        threading.Thread(target=work, name="vouch-run-" + run_id, daemon=True).start()
        return run_id

    def resume(self, run_id):
        with self.lock:
            job = self.items[run_id]
            if job["status"] == "running":
                raise ValueError("The run is already working.")
            settings, path = self.settings[run_id], job["db"]
        return self.start(*settings, existing=path, run_id=run_id)

    def snapshot(self, run_id):
        with self.lock:
            job = self.items.get(run_id)
            if job is None:
                raise KeyError(run_id)
            return {k: list(v) if k == "timeline" else v for k,v in job.items() if k != "db"}
