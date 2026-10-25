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

