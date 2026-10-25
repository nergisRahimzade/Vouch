# Nergis implementation record

Company data is simulated; rules and policies follow VOUCH_DE_EVENT_TAX_SPEC.md. Original base fixtures and sealed answers were not changed. The owner holdout was not opened.

## T1?T3 ? Existing work verified

### In simple words
- What: Reviewed the existing migration map, legacy tags, seeded company, date filtering, and tax calculations.
- Why: Later work needs a reproducible starting point.
- How: Read the specification and repository, verified payroll-legacy/event-ap-legacy, and ran the existing tests.
- Tests: Initial event-tax/ledger suite: 96 passed, 0 failed, 0 skipped.
- Needs your approval: Nothing.

### In software and finance terms
- Existing MAPPING.md, schema, Appendix A fixtures, deterministic generators, dated rules, Decimal tax functions, and period-lock tests were retained.
- This records verification, not authorship of pre-existing implementation.

## T4 ? Per-person ledgers and corrections

### In simple words
- What: Kept the existing T4 work and completed yearly allowance resets and retroactive corrections.
- Why: A later event or gift can change an earlier tax result.
- How: Recompute visible yearly rows, choose the two largest exemptions per year, and compare them with booked amounts.
- Tests: Included in the successful integration checkpoint: 206 passed, 0 failed, 0 skipped (189 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms
- calc/ledgers.py derives staff ledgers, payroll deltas, VAT reversals, and gift/37b reclasses; it preserves the pre-existing uncommitted T4 implementation and tests.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 ? 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T5 ? Entries, controls, detectors, and routing

### In simple words
- What: Replaced retired payroll/AP checks with event-tax entries and approval controls.
- Why: Correct numbers need balanced accounts, a safe period, and an independent reviewer.
- How: Use computed treatments, traceable sources, locked-period checks, approval fingerprints, and POL-MAT routing.
- Tests: Included in the successful integration checkpoint: 206 passed, 0 failed, 0 skipped (189 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms
- agent/templates.py, controls/*, and recon/checks.py implement double-entry drafts, VAT reversals, reclasses, role gates, standing policy, valid delegation, and evidence-bearing detectors.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 ? 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T6 ? Public scenarios and scorer

### In simple words
- What: Added the 15 public scenarios and a scorer that checks actual decisions.
- Why: Expected answers must remain separate from what the agent can see.
- How: Strip expect before running, then compare persisted results and source citations in the evaluator.
- Tests: Included in the successful integration checkpoint: 206 passed, 0 failed, 0 skipped (189 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms
- cases/public and evaluator/* implement listed-field comparison, EUR 0.01 tolerance, set matching, false-positive flags, safety checks, repeat-run scoring, and scorer-only external holdout loading.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 ? 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T7 ? Measured baseline

### In simple words
- What: Added and ran the benchmark command.
- Why: Speed and cost claims need measurements.
- How: Record time, model calls, tokens, cache tokens, tool time, and calculated API cost per scenario.
- Tests: Included in the successful integration checkpoint: 206 passed, 0 failed, 0 skipped (189 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms
- evaluator/bench.py wrote perf_baseline.json: Opus 5.5, high effort, one 15-case sequential measurement; 15/15 passed, 131.14 seconds summed case time, USD 0.703040. This one-run benchmark is not presented as a repeated score.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 ? 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T8 ? Pre-pass, typed tools, and agent loop

### In simple words
- What: Connected every event-tax calculation to the actual request/draft/review workflow.
- Why: The workflow must handle missing facts and safe failures, not only calculate totals.
- How: Compute first, ask recorded people, draft only permitted results, request review, and finish through submit_decision.
- Tests: Included in the successful integration checkpoint: 206 passed, 0 failed, 0 skipped (189 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms
- recon/run.py, agent/tools.py, runner.py, policy.py, schema.sql, and CLIs implement trigger dispatch, compact case files, deterministic proposals, Pydantic schemas, idempotent drafts, payroll exports, recipient notices, and actual holds on failure.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 ? 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T9 ? Performance implementation and validation gates

### In simple words
- What: Added measured concurrency, caching controls, and effort selection; kept unproven options opt-in.
- Why: A faster run must keep its accuracy and controls.
- How: Use source snapshots for parallel reads, isolated databases for concurrent cases, stable prefix caching, bounded retries, and measured reports.
- Tests: Included in the successful integration checkpoint: 206 passed, 0 failed, 0 skipped (189 deterministic and 17 live tests).
- Needs your approval: Owner holdout and cost-saving validation remain pending; T9 is partial.

### In software and finance terms
- The medium-effort public suite passed all 15 cases in all 3 runs in 86.21 seconds total with zero safety violations and USD 1.933234 calculated cost. The 60% cost-reduction and owner-holdout gates remain pending; no unsupported saving is claimed.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 ? 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T10 ? Attributed replies and tax-manager memory

### In simple words
- What: Made partial answers completable and reused recorded tax-manager decisions.
- Why: The agent must keep asking for missing evidence and remember an authorized answer.
- How: Preserve original receipts, append attributed replies, recompute documentation, and apply a stored permitted payroll method.
- Tests: Included in the successful integration checkpoint: 206 passed, 0 failed, 0 skipped (189 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms
- questions.py and Sources overlay immutable received/follow-up facts; learned_corrections are attributed to the tax manager or valid delegate. Resumed cases retain review flags and holds until authorized final approval.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 ? 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

## T11 ? API and imported frontend

### In simple words
- What: Fetched origin/frontend and connected its UI to real event-tax runs.
- Why: You asked for the UI, and the teammate needs the specified API.
- How: Keep the design, replace sample figures with API data, and expose scenario runs, SSE steps, review, books, payroll drafts, and audit records.
- Tests: Included in the successful integration checkpoint: 206 passed, 0 failed, 0 skipped (189 deterministic and 17 live tests).
- Needs your approval: Nothing.

### In software and finance terms
- ui/server.py and jobs.py implement spec ?10, CORS for localhost:5173, isolated SQLite jobs, custom scenarios, JSON endpoints, and SSE. frontend was imported from prototype commit 0480155; its retired backend changes were not merged.
- Live checkpoint: claude-opus-5-5, medium effort, 2026-10-25; public suite 15 cases ? 3 runs and P11 ? 5 consecutive runs. Expected tax amounts and thresholds were not changed.
- Root-cause changes are recorded in FIX_LOG.md. The obsolete payroll tests were adapted with your explicit approval; later failing assertions were left fixed while production code was repaired.

