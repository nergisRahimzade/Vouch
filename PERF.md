# Measured performance

All amounts below are API token costs computed from reported usage and published standard rates.
No holdout result or unmeasured saving is inferred.

## Recorded runs before the requested medium-effort switch

| Observation | Model | Effort | Cases ? runs | Passed | Wall time | Cost USD |
| --- | --- | --- | --- | --- | --- | --- |
| Initial sequential benchmark | claude-opus-5-5 | high | 15 ? 1 | 15/15 | 131.14 s (sum of case times) | 0.703040 |
| Repeated public score | claude-opus-5-5 | high | 15 ? 3 | 15/15 in every run | 91.72 s (whole suite) | 2.366437 |
| P11 rehearsal | claude-opus-5-5 | high | 1 ? 5 | 5 consecutive passes | 54.38 s (whole suite) | 0.674648 |
| Later repeated public score | claude-opus-5-5 | high | 15 ? 3 | 15/15 in every run | 90.72 s (whole suite) | 2.161306 |
| Later P11 rehearsal | claude-opus-5-5 | high | 1 ? 5 | Failed two calls after credit exhaustion | 43.66 s | 0.375257 |

The recorded successful suites have zero successful safety violations and zero extra non-info flags.
The failed rehearsal remains in results/; it is not replaced by an earlier success.

Baseline: perf_baseline.json. Detailed repeated reports: results/public-model-*.json.
A new full validation run with the user's updated Anthropic key and medium effort is in progress.

## Implemented controls for measurement

- Deterministic pre-pass and compact case file; all amounts come from code.
- Stable tool/system prefix caching with reported cache-read and cache-write token counters.
- Independent read tools run concurrently against a prepared snapshot; writes/audit appends remain ordered.
- Public cases can run concurrently with a semaphore of at most six and a private database per case.
- SDK retries are bounded; failures produce actual holds and explicit failed scores.
- Model/effort selection and an opt-in moving conversation-cache breakpoint support controlled experiments.

## Remaining gates

The public suite is below the eight-minute target in the recorded live runs. A 60% cost reduction
has not been demonstrated. The owner holdout path is not configured, so zero-regression holdout
validation is pending. Conversation caching remains opt-in; no unsupported cost-saving claim is made.
There is no model extraction path in the structured fixtures/reply workflow, so extraction-cache and
Haiku extraction accuracy measurements have not been performed. Prompt-audit tooling named in the
spec is not installed in this environment; it has not been claimed as run.

Anthropic and OpenAI both reported account credit/quota errors during testing. The user then replaced
the Anthropic key and explicitly requested Opus 5.5 at medium effort for the current validation.
