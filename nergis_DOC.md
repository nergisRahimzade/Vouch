# Nergis implementation record

Company data is simulated; rules and policies follow VOUCH_DE_EVENT_TAX_SPEC.md. Original base fixtures and sealed answers were not changed. The owner holdout was not opened.

## T1-T3 - Existing work verified

### In simple words

- What: Reviewed the existing migration map, legacy tags, seeded company, date filtering, and tax calculations.
- Why: Later work needs a reproducible starting point.
- How: Read the specification and repository, verified payroll-legacy/event-ap-legacy, and ran the existing tests.
- Tests: Initial event-tax/ledger suite: 96 passed, 0 failed, 0 skipped.
- Needs your approval: Nothing.

### In software and finance terms

- Existing MAPPING.md, schema, Appendix A fixtures, deterministic generators, dated rules, Decimal tax functions, and period-lock tests were retained.
- This records verification, not authorship of pre-existing implementation.

## T4 - Per-person ledgers and corrections

### In simple words

- What: Kept the existing T4 work and completed yearly allowance resets and retroactive corrections.
- Why: A later event or gift can change an earlier tax result.
- How: Recompute visible yearly rows, choose the two largest exemptions per year, and compare them with booked amounts.
- Tests: Included in the successful integration checkpoint: 210 passed, 0 failed, 0 skipped (193 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms

- calc/ledgers.py derives staff ledgers, payroll deltas, VAT reversals, and gift/37b reclasses; it preserves the pre-existing uncommitted T4 implementation and tests.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 x 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T5 - Entries, controls, detectors, and routing

### In simple words

- What: Replaced retired payroll/AP checks with event-tax entries and approval controls.
- Why: Correct numbers need balanced accounts, a safe period, and an independent reviewer.
- How: Use computed treatments, traceable sources, locked-period checks, approval fingerprints, and POL-MAT routing.
- Tests: Included in the successful integration checkpoint: 210 passed, 0 failed, 0 skipped (193 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms

- agent/templates.py, controls/*, and recon/checks.py implement double-entry drafts, VAT reversals, reclasses, role gates, standing policy, valid delegation, and evidence-bearing detectors.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 x 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T6 - Public scenarios and scorer

### In simple words

- What: Added the 15 public scenarios and a scorer that checks actual decisions.
- Why: Expected answers must remain separate from what the agent can see.
- How: Strip expect before running, then compare persisted results and source citations in the evaluator.
- Tests: Included in the successful integration checkpoint: 210 passed, 0 failed, 0 skipped (193 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms

- cases/public and evaluator/* implement listed-field comparison, EUR 0.01 tolerance, set matching, false-positive flags, safety checks, repeat-run scoring, and scorer-only external holdout loading.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 x 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T7 - Measured baseline

### In simple words

- What: Added and ran the benchmark command.
- Why: Speed and cost claims need measurements.
- How: Record time, model calls, tokens, cache tokens, tool time, and calculated API cost per scenario.
- Tests: Included in the successful integration checkpoint: 210 passed, 0 failed, 0 skipped (193 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms

- evaluator/bench.py wrote perf_baseline.json: Opus 5.5, high effort, one 15-case sequential measurement; 15/15 passed, 131.14 seconds summed case time, USD 0.703040. This one-run benchmark is not presented as a repeated score.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 x 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T8 - Pre-pass, typed tools, and agent loop

### In simple words

- What: Connected every event-tax calculation to the actual request/draft/review workflow.
- Why: The workflow must handle missing facts and safe failures, not only calculate totals.
- How: Compute first, ask recorded people, draft only permitted results, request review, and finish through submit_decision.
- Tests: Included in the successful integration checkpoint: 210 passed, 0 failed, 0 skipped (193 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms

- recon/run.py, agent/tools.py, runner.py, policy.py, schema.sql, and CLIs implement trigger dispatch, compact case files, deterministic proposals, Pydantic schemas, idempotent drafts, payroll exports, recipient notices, and actual holds on failure.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 x 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T9 - Performance implementation and validation gates

### In simple words

- What: Added measured concurrency, caching controls, and effort selection; kept unproven options opt-in.
- Why: A faster run must keep its accuracy and controls.
- How: Use source snapshots for parallel reads, isolated databases for concurrent cases, stable prefix caching, bounded retries, and measured reports.
- Tests: Included in the successful integration checkpoint: 210 passed, 0 failed, 0 skipped (193 deterministic and 17 live tests).
- Needs your approval: Owner holdout validation remains pending; T9 is partial.

### In software and finance terms

- The medium-effort public suite passed all 15 cases in all 3 runs in 86.21 seconds total with zero safety violations and USD 1.933234 calculated cost. The final opt-in public comparison met the cost target (65.08% reduction); the owner holdout gate remains pending, so these options remain opt-in.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 x 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T10 - Attributed replies and tax-manager memory

### In simple words

- What: Made partial answers completable and reused recorded tax-manager decisions.
- Why: The agent must keep asking for missing evidence and remember an authorized answer.
- How: Preserve original receipts, append attributed replies, recompute documentation, and apply a stored permitted payroll method.
- Tests: Included in the successful integration checkpoint: 210 passed, 0 failed, 0 skipped (193 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms

- questions.py and Sources overlay immutable received/follow-up facts; learned_corrections are attributed to the tax manager or valid delegate. Resumed cases retain review flags and holds until authorized final approval.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 x 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T11 - API and imported frontend

### In simple words

- What: Fetched origin/frontend and connected its UI to real event-tax runs.
- Why: You asked for the UI, and the teammate needs the specified API.
- How: Keep the design, replace sample figures with API data, and expose scenario runs, SSE steps, review, books, payroll drafts, and audit records.
- Tests: Included in the successful integration checkpoint: 210 passed, 0 failed, 0 skipped (193 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms

- ui/server.py and jobs.py implement spec ?10, CORS for localhost:5173, isolated SQLite jobs, custom scenarios, JSON endpoints, and SSE. frontend was imported from prototype commit 0480155; its retired backend changes were not merged.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 x 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T12 - Demo rehearsal and final verification

### In simple words

- What: Rehearsed the four demo moments and tested P11 five times in a row.
- Why: The demo must show actual repeatable behavior.
- How: Run the same trigger flow with scripted people and with the real Opus model, retaining every measured report.
- Tests: Included in the successful integration checkpoint: 210 passed, 0 failed, 0 skipped (193 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms

- The medium-effort live P11 rehearsal passed five consecutive runs with zero safety violations. P11/P01/P03/P10 also passed a five-run scripted rehearsal; all results are saved under results/.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 x 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.


## Additional requirement - ten years of books

### In simple words

- What: The Books view must show the project's books/entries for the last ten calendar years.
- Why: You explicitly requested a longer history.
- How: Filter actual recorded entries by year and clearly identify years with no recorded entries; do not generate fictional historical bookings.
- Tests: Added with the API/UI implementation below; final counts will be recorded after execution.
- Needs your approval: Nothing; this requirement was explicitly requested.

### In software and finance terms

- Add a rolling ten-year reporting window to the journal endpoint and Books UI. The supplied fixtures currently contain 2026 history, so earlier years are shown as unavailable, not as an asserted zero financial balance.

## Confirmed architecture - single-agent

### In simple words

- What: Vouch remains one accountant agent.
- Why: You explicitly asked to keep it single-agent.
- How: Claude/GPT select the model for the same loop; calculations stay in deterministic tools and approvals stay with people.
- Tests: The existing agent-loop, model-adapter, and permission tests cover this design. No collaborating agent loops were added.
- Needs your approval: Nothing.

### In software and finance terms

- D26 records the single-agent architecture in CLAUDE.md. Tool concurrency runs independent deterministic reads; it does not create autonomous agents. Scripted colleagues are attributed test responses, not independent agents.

## Final verification and added requirements

### In simple words

- What: Completed the event-tax layers, connected UI, real-agent tests, single-agent constraint, and ten-year Books view.
- Why: The result must be reviewable and supported by actual runs.
- How: Run the complete suite, replay the demo, check real HTTP/SSE behavior, and inspect a browser screenshot. Corrected currency-display encoding found by that browser check.
- Tests: 210 passed, 0 failed, 0 skipped; the subsequent deterministic selection passed 193 tests (17 live tests intentionally not selected in that development-only run). JavaScript syntax, EUR display, and local HTTP smoke checks passed.
- Needs your approval: Nothing for completed implementation. Owner holdout data is still required for T9's final adoption gate.

### In software and finance terms

- Live model: claude-opus-5-5, medium effort, 2026-10-25. Public 15 x 3 and P11 x 5 pass. The optimized public trial also has zero safety violations and exact required monetary outcomes.
- Additional GPT option: gpt-6.1-sol via Responses, sharing the same tools/gates. Protocol and failure-control tests pass; live GPT validation was rejected by the OpenAI account's insufficient quota and is not claimed as successful.
- /api/books and /api/entries expose a default 2017?2026 window for 2026 scenarios. The supplied data has entries in 2026; the other nine years explicitly show no recorded entries. No historical financial rows were fabricated.
- Core fixes, failed checks, and repaired classes of problem are recorded in FIX_LOG.md. Expected tax amounts, base fixtures, thresholds, and sealed answer entries were not changed. The approved public documentation placeholders were replaced with actual scenario attendee IDs.
- Local task branches/commits are cumulative through v3-t12-demo. No changes have been pushed to a remote.

Final owner-holdout status: the user configured the set and approved live scenario-input transfer to Anthropic. The scorer stopped during loading with ScenarioError. No holdout accuracy result is claimed; private cases and expected answers were not inspected or changed. T9 remains pending investigation of that loading failure.

Additional final regression: proper German Unicode in a generic meal description was verified in a new passing test; the prior full-suite result remains 210 passed, and no later full-suite count is claimed.

## Jev provider and UI completion attempt — 2026-10-25

### In simple words
- What: Connected Jev to the existing accountant, made the dashboard start live runs by default, and fixed the person lookup form. Preserved the supplied dashboard, case, approval, books and audit screens.
- Why: Jev makes fixed-choice decisions rather than writing chat messages. Sending it Claude's request format would not work. A wrong key must stop safely without showing a technical error to the user.
- How: Jev chooses a treatment and final action from documented choices. The same existing tools ask for missing records, compute money, create drafts and require human reviews. No model can invent amounts or approve its own work.
- Tests: 214 deterministic checks passed, 0 failed; 17 billed live tests deselected. JavaScript syntax passed. The real HTTP server served all UI assets, all four demo flows, streams, approvals, payroll and people records. Custom scripted event returned 59.50 per participant. Explicitly mocked Jev choices passed the unchanged scorer for all four demos; this proves adapter plumbing, not live model accuracy.
- Needs your action: The supplied Jev key was rejected by the official TypeSafe endpoint with HTTP 401, including repeated preflight. Replace it locally. Live public accuracy, all 12 live demo repetitions and the required live approval/escalation completion remain unverified. Nothing was read from the new holdout.

### In software and finance terms
- `vouch/agent/jev_backend.py` implements TypeSafe POST /v1/systemone choice questions, strict Pydantic answer parsing, enum membership validation, bounded retry/backoff and fail-closed holds. Existing Pydantic tool schemas, Decimal tax calculations, POL-MAT routing, citation guards and segregation of duties remain authoritative. Source text remains untrusted. Classification and final action are real Jev decisions when authenticated; orchestration and accounting narrative use deterministic records.
- `policy.py` loads JEV_API_KEY/TYPESAFE_API_KEY without printing them, resolves explicit model/provider settings before credential-based defaults, dispatches Jev, records resolved model/token usage and uses the published input-only Jev tariff. `chat_backend.py` recognizes VOUCH_PROVIDER=jev; `__main__.py` exposes Jev readiness through the existing preflight target. No Anthropic cache or effort fields are sent to Jev.
- Jev authentication rejection is audited by provider/model and HTTP status; the live HTTP run returned `paused: model unavailable` with a friendly message and sanitized decision failure. No Vouch entry was posted. No live accuracy or credit expenditure is inferred from failed authentication. DeepSeek and Kimi keys were not configured; neither model was evaluated.
- Definition-of-done checklist: (a) FAIL, preflight HTTP 401; (b) PASS, GET scenarios includes P11/P01/P03/P10; (c) FAIL live proof blocked, scripted HTTP streams/approval/entries/payroll/people smoke passed, tax-manager escalation answer/resumption and posting completion still outstanding; (d) FAIL live custom proof blocked, scripted custom passed; (e) FAIL no valid new live public score, scores endpoint HTTP 200 and existing invalid-run filtering retained; (f) authentication-outage safe pause PASS, exact mid-run bad-host/fallback proof outstanding; (g) PASS 214 deterministic checks and JS syntax.
- Next steps after an active key: run preflight, public suite three repetitions and all four demos three repetitions; resolve any measured production failures without changing expectations; complete live human escalation/resumption and posting HTTP proof, then new owner holdout through scorer only. Do not start cost trimming, evaluation-loop work, case generation, rule-source relabeling or old-holdout migration. Single-agent architecture preserved. No push.
