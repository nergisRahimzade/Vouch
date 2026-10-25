"""Record a live close's model replies, and replay them later for free.

A recording is the model's replies, keyed by case and turn, saved as JSON lines. Replaying feeds the same replies
back through the same code, so the dashboard, the controls, the audit trail and the report can be checked again
without paying for the model. A replay is never a score and is labelled everywhere: its model is shown as
"replay of <model>" and its cost is $0.00, because nothing was spent. If the code now asks for a reply the
recording does not have (because the code changed), the replay stops with a plain reason instead of guessing."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace


def _key(kwargs: dict) -> str:
    if kwargs.get("tools"):
        first = kwargs["messages"][0]["content"]
        text = first[0]["text"] if isinstance(first, list) else first
        case = json.loads(text.split("<case>", 1)[1].split("</case>", 1)[0])
        return f"case:{case['finding_id']}"
    system = kwargs.get("system")
    head = (system if isinstance(system, str) else json.dumps(system, default=str))[:40]
    return f"call:{head}"


def _turn(kwargs: dict) -> int:
    return sum(1 for m in kwargs.get("messages", []) if m["role"] == "assistant")


class RecordingClient:
    """Wraps the real client and appends every reply to `path`."""
    def __init__(self, client, path: Path):
        self.client, self.path, self.lock = client, Path(path), threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        response = self.client.beta.messages.create(**kwargs)
        line = {"key": _key(kwargs), "turn": _turn(kwargs), "response": response.model_dump(mode="json")}
        with self.lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return response


def _namespace(value):
    """A recorded reply back into the shape the API returns: attributes, except a tool call's input, which the
    agent reads as a plain dictionary exactly as the API gives it."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: (v if k == "input" else _namespace(v)) for k, v in value.items()})
    if isinstance(value, list):
        return [_namespace(v) for v in value]
    return value


class NotRecorded(RuntimeError):
    """The code asked for a reply the recording does not have."""


class ReplayClient:
    """Answers each call with the recorded reply for the same case and turn."""
    def __init__(self, path: Path):
        self.replies, self.lock = {}, threading.Lock()
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                self.replies.setdefault((item["key"], item["turn"]), []).append(item["response"])
        self.model = next(iter(self.replies.values()))[0].get("model", "unknown") if self.replies else "unknown"
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        key = (_key(kwargs), _turn(kwargs))
        with self.lock:
            queue = self.replies.get(key)
            if not queue:
                raise NotRecorded(f"the recording has no reply for {key[0]} turn {key[1]}; the code asked for "
                                  "something the live run did not (record a new run)")
            return _namespace(queue.pop(0) if len(queue) > 1 else queue[0])
