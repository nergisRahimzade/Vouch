"""OpenAI-compatible Chat Completions providers (DeepSeek V4 Pro, Kimi K2.6) behind the same tools and limits.

Configured from the environment: VOUCH_PROVIDER, VOUCH_BASE_URL, VOUCH_MODEL, VOUCH_API_KEY for the primary and
VOUCH_BACKUP_PROVIDER, VOUCH_BACKUP_BASE_URL, VOUCH_BACKUP_MODEL, VOUCH_BACKUP_API_KEY for the backup. Anthropic-only
features (prompt cache_control, effort) are not sent. A provider that keeps failing after the SDK's retries is
replaced by the backup, recorded in the audit trail; if both fail the run pauses ("paused: model unavailable")."""
import json
import os

PROVIDERS = {"jev": ("https://api.typesafe.ai/v1", "jev-latest"), "deepseek": ("https://api.deepseek.com", "deepseek-v4-pro"),
             "kimi": ("https://api.moonshot.ai/v1", "kimi-k2.6")}
PAUSED = "paused: model unavailable"
MODEL_ERROR = "hold: model error"


def config(prefix="VOUCH_"):
    """One provider's settings, or None when nothing is configured under that prefix."""
    provider = os.environ.get(prefix + "PROVIDER")
    if not provider and not os.environ.get(prefix + "BASE_URL"):
        return None
    base, model = PROVIDERS.get(provider or "", (None, None))
    return {"provider": provider or "openai_compatible", "base_url": os.environ.get(prefix + "BASE_URL") or base,
            "model": os.environ.get(prefix + "MODEL") or model, "api_key": os.environ.get(prefix + "API_KEY") or ""}


def configured():
    return [c for c in (config("VOUCH_"), config("VOUCH_BACKUP_")) if c]


def client_for(cfg, timeout=90):
    from openai import OpenAI
    if not cfg["api_key"]:
        raise ValueError(f"{cfg['provider']}: no API key is configured")
    return OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=timeout, max_retries=3)


def tools(specs):
    return [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                              "parameters": s["input_schema"]}} for s in specs]


def unavailable(error):
    """Credit, rate-limit, server and connection failures: worth trying the backup provider."""
    import openai
    if isinstance(error, (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError,
                          openai.InternalServerError, openai.AuthenticationError, openai.PermissionDeniedError)):
        return True
    return isinstance(error, openai.APIStatusError) and error.status_code in {402, 408, 409, 429} | set(range(500, 600))


def create(policy, toolbox, messages):
    import openai
    from vouch.agent.policy import PolicyFailed
    from vouch.controls import audit, gates
    while True:
        try:
            response = policy.client.chat.completions.create(model=policy.model, messages=messages,
                                                             tools=policy.tools, tool_choice="auto", max_tokens=4096)
            break
        except (openai.APIError, ValueError) as error:
            reason = f"{type(error).__name__} from {policy.provider}"
            if not (isinstance(error, ValueError) or unavailable(error)):
                raise PolicyFailed(MODEL_ERROR + f" ({reason})") from error
            backup = policy.backups.pop(0) if policy.backups else None
            audit.log(toolbox.conn, gates.VOUCH, "model_switch" if backup else "model_unavailable",
                      toolbox.case_id if toolbox.context else "trigger", "failed" if not backup else "done",
                      f"{policy.provider}/{policy.model} unavailable after retries" +
                      (f"; switching to {backup['provider']}/{backup['model']}" if backup else "; pausing the run"),
                      {"from": {"provider": policy.provider, "model": policy.model}, "error_type": type(error).__name__,
                       "to": {k: backup[k] for k in ("provider", "model")} if backup else None})
            toolbox.conn.commit()
            if backup is None:
                raise PolicyFailed(PAUSED) from error
            policy.use(backup)
    policy.calls += 1
    usage = response.usage
    policy.usage.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
    policy.usage.output_tokens += getattr(usage, "completion_tokens", 0) or 0
    policy.usage.cache_read_tokens += getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    return response


def work(policy, case, toolbox):
    from vouch.agent.policy import MAX_TURNS, PolicyFailed, record_model_call
    messages = [{"role": "system", "content": policy.system},
                {"role": "user", "content": json.dumps({"trigger_id": case["case_id"]})}]
    malformed = 0
    for _ in range(MAX_TURNS):
        response = create(policy, toolbox, messages)
        choice = response.choices[0]
        message = choice.message
        calls = list(message.tool_calls or [])
        record_model_call(toolbox, policy, choice.finish_reason, response.usage, [c.function.name for c in calls],
                          [message.content] if message.content else [])
        if not calls:
            raise PolicyFailed(MODEL_ERROR + " (no tool call and no decision)")
        messages.append({"role": "assistant", "content": message.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                        for c in calls]})
        parsed, bad = [], {}
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
                parsed.append((c, args))
            except ValueError as error:
                bad[c.id] = {"status": "error", "ok": False, "warnings": [], "error": f"malformed arguments: {error}"}
        results = dict(zip([c.id for c, _ in parsed], toolbox.call_many([(c.function.name, a) for c, a in parsed])))
        results.update(bad)
        schema_errors = sum(1 for r in results.values() if not r["ok"] and
                            ("malformed arguments" in r["error"] or "validation error" in r["error"]))
        malformed = malformed + 1 if schema_errors else 0
        if malformed >= 2:      # the error was sent back once; a second malformed turn ends the case safely
            raise PolicyFailed(MODEL_ERROR + " (tool arguments did not match the schema twice)")
        for c in calls:
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(results[c.id])})
        if toolbox.submitted is not None:
            return
    raise PolicyFailed(MODEL_ERROR + " (exceeded bounded tool turns)")


def preflight():
    """One tiny call to each configured provider. Returns [(name, ok, reason)]."""
    out = []
    for cfg in configured():
        name = f"{cfg['provider']}/{cfg['model']}"
        try:
            client = client_for(cfg, timeout=30)
            client.with_options(max_retries=1).chat.completions.create(
                model=cfg["model"], messages=[{"role": "user", "content": "Reply with OK."}], max_tokens=5)
            out.append((name, True, "GREEN"))
        except Exception as error:      # reported as a reason, never raised: preflight explains, it does not crash
            status = getattr(error, "status_code", None)
            reason = {401: "the API key was rejected", 402: "no credit left on the account",
                      403: "the key may not use this model", 404: "unknown model id or base URL",
                      429: "rate-limited or out of quota"}.get(status) or (
                      "no API key is configured" if isinstance(error, ValueError) else
                      f"cannot reach {cfg['base_url']}" if "Connection" in type(error).__name__ else type(error).__name__)
            out.append((name, False, reason))
    return out
