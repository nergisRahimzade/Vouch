# PLAN: Vouch v2 (event vendor payables)

Tasks from `VOUCH_EVENT_AP_SPEC.md` §11. One git branch per task, each branched from the one before.

| # | Branch | Task | Done when | Status |
|---|---|---|---|---|
| T1 | `v2-t1-mapping` | `MAPPING.md` + tag `payroll-legacy` | Every module mapped | done |
| T2 | `v2-t2-schema` | Schema + base fixtures (Appendix A) + time-travel loader | Seed reproducible; loader unit tests pass | |
| T3 | `v2-t3-matching` | Matching engine + unit tests (§5.2, boundaries) | All rules hand-checked | |
| T4 | `v2-t4-entries` | Entry types (deposit, release, accrual + reversal, reclass, discount) + period lock | Balance + lock tests pass | |
| T5 | `v2-t5-detectors` | Detectors + approval matrix + SoD + delegation + aggregation | Unit tests pass | |
| T6 | `v2-t6-scenarios` | Scenario loader, public cases P01–P15, C01–C04, scorer updates, holdout loader (aggregates only) | Scorer runs on public set | |
| T7 | `v2-t7-bench` | Step 0 baseline bench | `perf_baseline.json` saved | |
| T8 | `v2-t8-agent` | Agent tools (§7) + deterministic pre-pass + case file + loop | Public suite runs end to end | |
| T9 | `v2-t9-speed` | Speed steps 3–9 one by one, measured | Targets in §9 met, no regressions | |
| T10 | `v2-t10-memory` | Memory: vendor history, learned corrections | P15 + rejection handling pass | |
| T11 | `v2-t11-ui` | UI updates | Views in §10 | |
| T12 | `v2-t12-demo` | Demo: P01 + P02 scripted run, real-vs-simulated slide, P01 passes 5× | Rehearsed | |
