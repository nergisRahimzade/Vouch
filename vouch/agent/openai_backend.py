"""OpenAI Responses adapter using the same deterministic tools, controls and decision contract."""
import json
import os


def add_usage(policy, usage):
    if usage is None:
        return
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    created = getattr(details, "cache_creation_tokens", 0) or 0
    policy.usage.input_tokens += max(0, usage.input_tokens - cached - created)
    policy.usage.cache_read_tokens += cached
    policy.usage.cache_write_tokens += created
    policy.usage.output_tokens += usage.output_tokens


def create(policy, **kwargs):
    import openai
    from vouch.agent.policy import PolicyFailed
    try:
        response = policy.client.responses.create(model=policy.model, reasoning={"effort":policy.effort},
                      store=False, max_output_tokens=4096, **kwargs)
    except (openai.APIError, TimeoutError) as error:
        detail = getattr(error, "message", str(error))
        secret = os.environ.get("OPENAI_API_KEY")
        if secret:
            detail = detail.replace(secret, "[redacted]")
        raise PolicyFailed(f"OpenAI API failed after bounded retries: {type(error).__name__}: {detail}") from error
    policy.calls += 1
    add_usage(policy, response.usage)
    if response.status in {"failed", "incomplete", "cancelled"}:
        raise PolicyFailed("OpenAI response did not complete: " + str(response.status))
    return response


def work(policy, case, toolbox):
    from vouch.agent.policy import MAX_TURNS, SYSTEM, PolicyFailed
    history = [{"role":"user", "content":json.dumps({"trigger_id":case["case_id"]})}]
    for _ in range(MAX_TURNS):
        response = create(policy, instructions=SYSTEM, input=history, tools=policy.tools)
        # Preserve reasoning, encrypted reasoning and assistant phases, not only function calls.
        history.extend(item.model_dump(exclude_none=True) for item in response.output)
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            raise PolicyFailed("GPT returned without submit_decision")
        try:
            requests = [(item.name, json.loads(item.arguments)) for item in calls]
        except (TypeError, json.JSONDecodeError) as error:
            raise PolicyFailed("GPT returned invalid tool arguments") from error
        results = toolbox.call_many(requests)
        for call, result in zip(calls, results):
            history.append({"type":"function_call_output", "call_id":call.call_id, "output":json.dumps(result)})
        if toolbox.submitted is not None:
            return
    raise PolicyFailed("GPT exceeded bounded tool turns")


def defend(policy, question, rows, problems):
    response = create(policy, instructions="Answer only from the provided audit records. Copy amounts and citations exactly.",
                      input=json.dumps({"question":question,"records":rows,"problems":problems}))
    return response.output_text
