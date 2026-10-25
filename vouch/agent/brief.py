"""The case brief: what the model would otherwise spend its first turns fetching, put in front of it by code.

For every case the model used to open with lookups: the case's current state, the text of each rule the case
cites, and the earlier rulings about its kind of case. Each lookup is a round trip, and every round trip
re-sends the conversation. The brief makes those same lookups up front, through the same tools, so they are
logged in the audit trail (marked as prepared by Vouch, not asked for by the model) and join the evidence the
claim check reads. The model sees the full text of every rule and ruling, never a summary, and keeps every tool
for anything else it wants to check."""
from __future__ import annotations

import json

from vouch.agent import lessons
from vouch.agent.tools import Toolbox


def _json(value) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False, default=str)


def build(toolbox: Toolbox, case: dict) -> str:
    """The brief for one case, from tool results only."""
    case_id = case["finding_id"]
    rules = {}
    for rule_id in case.get("rules") or []:
        result = toolbox.call("get_policy", {"rule_id": rule_id}, prefetched=True)
        rules[rule_id] = result["rule"] if result["ok"] else f"(could not be read: {result['error']})"
    rulings = toolbox.call("get_precedents", {"kind": case["kind"]}, prefetched=True)
    state = toolbox.call("get_case", {"case_id": case_id}, prefetched=True)
    learned = lessons.for_kind(case["kind"])
    return "\n".join([
        "<policy_rules>", _json(rules), "</policy_rules>",
        "<earlier_rulings_of_this_kind>", _json(rulings.get("rulings", [])), "</earlier_rulings_of_this_kind>",
        "<case_state_now>", _json({k: state.get(k) for k in ("holds", "questions", "requests", "drafts")}),
        "</case_state_now>",
        *(["<lessons_approved_by_the_controller>", _json(learned), "</lessons_approved_by_the_controller>"]
          if learned else [])])
