"""A case whose model call can't get through for a moment (network, timeout, rate limit, overload) is tried once
more at the end of the step instead of being held at once. A case that still can't get through is held with the
plain reason. A real refusal or the spending cap is never retried."""
import shutil
from types import SimpleNamespace

import anthropic

from vouch.agent.policy import ModelPolicy
from vouch.agent.runner import run_close
from vouch.controls import audit
from vouch.db import connect


class _FlakyClient:
    """Drops the connection on the first call for the first `flaky` cases it sees (or always, if `forever`)."""
    def __init__(self, flaky=1, forever=False, status=None):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.seen, self.flaky, self.forever, self.status = {}, flaky, forever, status

    def create(self, **kwargs):
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if kwargs.get("tools"):
            first = kwargs["messages"][0]["content"][0]["text"]
            key = first[:400]
            count = self.seen.setdefault(key, 0)
            self.seen[key] = count + 1
            first_cases = list(self.seen)[:self.flaky]
            if key in first_cases and (count == 0 or self.forever):
                if self.status:
                    raise anthropic.InternalServerError(message="overloaded", response=SimpleNamespace(
                        status_code=self.status, headers={}, request=None), body=None)
                raise anthropic.APIConnectionError(message="Connection error.", request=None)
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text="Nothing further to do.")])


def _run(db_path, tmp_path, client, monkeypatch):
    waits = []
    monkeypatch.setattr("vouch.agent.runner.time.sleep", waits.append)         # no real waiting in a test
    path = tmp_path / "v.db"
    shutil.copyfile(db_path, path)
    conn = connect(path)
    result = run_close(conn, ModelPolicy(client=client, parallel_cases=4))
    return conn, result, waits


def test_a_dropped_connection_is_retried_and_the_case_is_not_held(db_path, tmp_path, monkeypatch):
    conn, result, waits = _run(db_path, tmp_path, _FlakyClient(flaky=2), monkeypatch)
    try:
        assert result["failures"] == {}
        retried = [r for r in audit.trail(conn) if r["action"] == "case_retry_later"]
        assert len(retried) == 2 and all("could not be reached" in r["reason"] for r in retried)
        assert waits == [10]                                    # one round, after a short wait
        assert audit.verify(conn).ok
    finally:
        conn.close()


def test_a_case_that_still_cannot_get_through_is_held_with_the_plain_reason(db_path, tmp_path, monkeypatch):
    conn, result, waits = _run(db_path, tmp_path, _FlakyClient(flaky=1, forever=True), monkeypatch)
    try:
        assert len(result["failures"]) == 1
        (cid, reason), = result["failures"].items()
        assert "could not be reached" in reason
        assert waits == [10, 30, 60]                            # three tries, each after a longer wait
        assert conn.execute("SELECT status FROM cases WHERE case_id = ?", (cid,)).fetchone()[0] == "held"
    finally:
        conn.close()


def test_an_overloaded_api_is_retried_too(db_path, tmp_path, monkeypatch):
    conn, result, waits = _run(db_path, tmp_path, _FlakyClient(flaky=1, status=529), monkeypatch)
    try:
        assert result["failures"] == {}
    finally:
        conn.close()
