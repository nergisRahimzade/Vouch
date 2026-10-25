# PLAN: Vouch v3 (German event tax, Nordlicht Maschinenbau GmbH)

Tasks from `VOUCH_DE_EVENT_TAX_SPEC.md` §11. One git branch per task (`v3-tN-...`), each branched from
the one before. The earlier event-AP plan (branches `v2-t1`..`v2-t5`, tag `event-ap-legacy`) stopped at
its T5 when v3 superseded it; its domain-neutral work is reused here.

| # | Branch | Task | Done when | Status |
|---|---|---|---|---|
| T1 | `v3-t1-mapping` | `MAPPING.md` + legacy tags | Every module mapped | done |
| T2 | `v3-t2-schema` | Schema, rules table, base fixtures, employee generator, time travel (reuse) | Seed reproducible | done |
| T3 | `v3-t3-engine` | Event tax engine + unit tests (§6.1, POL-ROUND) | All rules hand-checked | done |
| T4 | `v3-t4-ledgers` | Per-person ledgers, optimizer, corrections + tests | Retro cases pass in unit tests | done |
| T5 | `v3-t5-detectors` | Entries, period lock, detectors, routing | Unit tests pass | done |
| T6 | `v3-t6-scenarios` | Scenario loader updates, public cases P01–P11 + C01–C04, scorer fields, holdout loader | Scorer runs on public set | done |
| T7 | `v3-t7-bench` | Bench baseline | `perf_baseline.json` | done |
| T8 | `v3-t8-agent` | Tools, pre-pass, case file, agent loop | Public suite end to end | done |
| T9 | `v3-t9-speed` | Speed steps, measured | §9 targets met | partial: cost/owner-holdout gates pending |
| T10 | `v3-t10-memory` | Memory: learned corrections from tax manager answers | Escalate → answer → follow works | done |
| T11 | `v3-t11-api` | API (§10) incl. SSE + custom scenario | Frontend can connect | done |
| T12 | `v3-t12-demo` | Demo: P11, P01, P03, P10 scripted; P11 passes 5× | Rehearsed | |
