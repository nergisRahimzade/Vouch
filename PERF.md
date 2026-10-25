# Measured performance

All amounts below are API token costs computed from reported usage and published standard rates.
No holdout result or unmeasured saving is inferred.

## Recorded runs before the requested medium-effort switch

| Observation | Model | Effort | Cases ? runs | Passed | Wall time | Cost USD |
| --- | --- | --- | --- | --- | --- | --- |
| Initial sequential benchmark | claude-opus-5-5 | high | 15 x 1 | 15/15 | 131.14 s (sum of case times) | 0.703040 |
| Repeated public score | claude-opus-5-5 | high | 15 x 3 | 15/15 in every run | 91.72 s (whole suite) | 2.366437 |
| P11 rehearsal | claude-opus-5-5 | high | 1 x 5 | 5 consecutive passes | 54.38 s (whole suite) | 0.674648 |
| Later repeated public score | claude-opus-5-5 | high | 15 x 3 | 15/15 in every run | 90.72 s (whole suite) | 2.161306 |
| Later P11 rehearsal | claude-opus-5-5 | high | 1 x 5 | Failed two calls after credit exhaustion | 43.66 s | 0.375257 |

The recorded successful suites have zero successful safety violations and zero extra non-info flags.
The failed rehearsal remains in results/; it is not replaced by an earlier success.

Baseline: perf_baseline.json. Detailed repeated reports: results/public-model-*.json.
The final medium-effort validation is recorded below.

## Implemented controls for measurement

- Deterministic pre-pass and compact case file; all amounts come from code.
- Stable tool/system prefix caching with reported cache-read and cache-write token counters.
- Independent read tools run concurrently against a prepared snapshot; writes/audit appends remain ordered.
- Public cases can run concurrently with a semaphore of at most six and a private database per case.
- SDK retries are bounded; failures produce actual holds and explicit failed scores.
- Model/effort selection and an opt-in moving conversation-cache breakpoint support controlled experiments.

## Remaining gates

The public suite is below the eight-minute target in the recorded live runs. The final controlled public comparison below demonstrates the cost target. The owner holdout path is not configured, so zero-regression holdout
validation is pending. Conversation caching remains opt-in; no unsupported cost-saving claim is made.
There is no model extraction path in the structured fixtures/reply workflow, so extraction-cache and
Haiku extraction accuracy measurements have not been performed. Prompt-audit tooling named in the
spec is not installed in this environment; it has not been claimed as run.

Anthropic and OpenAI both reported account credit/quota errors during testing. The user then replaced
the Anthropic key and explicitly requested Opus 5.5 at medium effort for the current validation.

## Final medium-effort validation

- Full suite: **210 passed, 0 failed, 0 skipped** in **176.70 seconds**; 193 deterministic tests and 17 billed live tests. A subsequent deterministic run passed all 193 selected tests.
- Current model: claude-opus-5-5; effort: medium. The public suite passed 15/15 cases in all three live runs; P11 passed five consecutive live runs. Both have zero safety violations.
- Default-path public score: 96.27 seconds for 15 cases ? 3 runs; calculated token cost USD 1.698175.
- Opt-in conversation cache + ordered action batching: 61.21 seconds for the same 15 cases ? 3 runs; USD 0.592980; average 3.51 model calls/case; 100% catch rate, 0% false-positive rate, 100% amount accuracy.

Measured cost reduction against that matched medium-effort run: **65.08%**. Public speed, cost, accuracy, and safety targets are met.

Source reports:

- [Default public run](results/public-model-2026-10-25T103740371540+0000.json)
- [Optimized public trial](results/public-model-2026-10-25T104022388671+0000.json)
- [Final five-run P11 rehearsal](results/public-model-2026-10-25T103916722217+0000.json)

The owner holdout gate remains pending until VOUCH_HOLDOUT_PATH is configured. Optimization flags
remain opt-in until that gate passes. The external claude-api prompt-audit command is unavailable;
prompt_audit.json records the actual deterministic tool/schema audit and explicitly labels its scope.

Final owner-holdout status: the user configured the set and approved live scenario-input transfer to Anthropic. The scorer stopped during loading with ScenarioError. No holdout accuracy result is claimed; private cases and expected answers were not inspected or changed. T9 remains pending investigation of that loading failure.

## Agent-loop redesign: the model chooses (2026-10-25)

Measured with claude-opus-5-5, medium effort, after the redesign in FIX_LOG.md (the model now chooses the
classification, flags, questions, escalations, reviewers and the decision; code computes and enforces).

| Observation | Cases x runs | Result | Time | Cost USD |
| --- | --- | --- | --- | --- |
| Live public, run 1 (valid) | 15 x 1 | 13/15 passed; 0 safety violations | 386.2 s summed case time | 2.358966 |
| Live public, runs 2-3 | 15 x 2 | not measured: the Anthropic account ran out of credit ("credit balance is too low") | — | 0.138959 |
| Live owner holdout | 16 x 3 | not measured: every call refused for credit; zero model calls | — | 0.00 |
| Scripted double, public | 15 x 3 | 15/15 every run | — | 0 |
| Scripted double, holdout | 16 x 3 | 16/16 every run (after reading the holdout failures; not an unseen measure) | — | 0 |

The two valid-run misses were judgment, not arithmetic: P04 added the controller for a payroll delta, and P09
held the gift while escalating an election email. The prompt was corrected to state POL-MAT and §5.9 exactly;
this correction has not been re-measured live. Cost per case rose from about USD 0.04 to about USD 0.16: the
model reads the full case file and takes about 5 turns. Reports
`results/public-model-2026-10-25T115112257347+0000.json` and `results/holdout-model-2026-10-25T115249630058+0000.json`
contain the credit-refused runs and must not be read as accuracy results.
