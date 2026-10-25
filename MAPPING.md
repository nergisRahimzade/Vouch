# MAPPING: to v3, German event tax (Nordlicht Maschinenbau GmbH)

Every file in the repo when v3 started (branch `v2-t5-detectors`, tagged `event-ap-legacy`), what it
does today, and what it becomes in v3. Two earlier domains are retired: payroll (tag `payroll-legacy`,
already off the main path) and event vendor payables (tag `event-ap-legacy`). Work from the AP spec that
is not AP-specific is kept and reused, as `VOUCH_DE_EVENT_TAX_SPEC.md` §0.1 asks: the scenario format and
time-travel loader, the ledger with its balance check and period lock, the rounding helper, the
untrusted-text screening.

Change types: **keep** (unchanged or only domain words change), **adapt** (same role and pattern, new
domain logic), **replace** (same place and role, new contents), **retire** (removed from the main path;
still in its legacy tag), **new** (a file v3 adds inside an existing layer).

Layers, unchanged and in order: company world (fixtures, generator, scenarios) → system of record (db)
→ calculations (calc) → detectors and pre-pass (recon) → controls → agent → evaluator → API.

## Top level

| File | Today | v3 | Change |
|---|---|---|---|
| `CLAUDE.md` | Event AP rules and D18–D24 | Event tax description, glossary, chart, rules summary, routing, decisions D18–D25 (spec §12) | adapt |
| `VOUCH_EVENT_AP_SPEC.md` | AP spec | Superseded by `VOUCH_DE_EVENT_TAX_SPEC.md` | retire |
| `VOUCH_DE_EVENT_TAX_SPEC.md` | — | The v3 spec | new |
| `MAPPING.md` | AP mapping | This checklist | replace |
| `PLAN.md` | AP tasks T1–T12 | v3 tasks T1–T12 with status | replace |
| `PERF.md`, `perf_baseline.json` | — | Speed measurements (spec §9) | new |
| `README.md` | Payroll README | Seat line (§1 verbatim), commands, real vs simulated, controls, generated results | replace |
| `FIX_LOG.md` | Every fix with its root cause | Same; v3 fixes appended | keep |
| `pyproject.toml` | Deps incl. pyyaml | Adds `faker` (pinned, employee and partner names); API extra | adapt |
| `.gitignore` | Generated files | Same | keep |
| `cases/public/*.yaml` | — | Public cases P01–P11, C01–C04 (spec §8.2) | new |

## `vouch/` root

| File | Today | v3 | Change |
|---|---|---|---|
| `__init__.py` | AP docstring | Event tax docstring | adapt |
| `__main__.py` | Payroll commands | `rebuild`, `run`, `why`, `score`, `bench`, `api` | adapt |
| `money.py` | Exact decimal helpers, half-up cents | Same | keep |
| `dates.py` | Month helpers | Same | keep |

## Layer 1, company world: `vouch/world/`

| File | Today | v3 | Change |
|---|---|---|---|
| `fixtures.yaml` | AP Appendix A | v3 Appendix A, seeded exactly, plus the rules and policies of §4.4 | replace |
| `generator.py` | — | Deterministic employees (E001–E450, Faker de_DE seed 42) and business partners (BP-001–BP-600, seed 7) | new |
| `scenario.py` | Scenario loader: setup, overrides, time travel, `expect` dropped | Same loader; v3 tables and compact forms (attendance ranges, companions, payroll export ranges, processed history) | adapt |
| `build.py` | Rows for a scenario on a date | Same | keep |
| `__main__.py` | `python -m vouch.world build` | Same | keep |
| `clauses.py` | AP contract clause typing | No contracts in v3 | retire |

## Layer 2, system of record: `vouch/db/`

| File | Today | v3 | Change |
|---|---|---|---|
| `__init__.py` | connect/create | Same | keep |
| `schema.sql` | AP tables | v3 tables (§4.1): employees, business partners, events, attendance, gift distributions, invoices/lines, emails, elections (read-only), payroll exports, recipient notices, rules, policies | replace |
| `ledger.sql` | Journal entries/lines with event tags | Same; lines also carry the item and person they concern | adapt |
| `periods.sql` | Lock triggers, controller-only status change | Same | keep |
| `accounts.py` | AP chart | v3 SKR-style chart (§4.3) | replace |
| `ledger.py` | Entry/Line, balance check, refusals | Same; entry types for event tax | adapt |
| `periods.py` | Close on the 5th; open-period routing | Close on the 10th of M+1 (§5.7); same routing | adapt |
| `load.py` | Loads rows, history entries, locks closed months | Same | adapt |
| `rebuild.py` | Rebuild from fixtures/scenario | Same | keep |
| `subledger.py` | AP per vendor, prepaid per event | AP (1600) per vendor and input VAT (1400) per event roll up to the ledger | adapt |
| `rows.py` | Payroll row checks | Invoice arithmetic is validated in the engine | retire |
| `__main__.py` | rebuild CLI | Same | keep |

## Layer 3, calculations: `vouch/calc/`

| File | Today | v3 | Change |
|---|---|---|---|
| `matching.py` | AP matching engine | Replaced by the event tax engine | retire |
| `eventtax.py` | — | Event tax engine (§6.1): staff-event allocation, VAT test, hospitality split, gift ledger, §40 and §37b flat taxes, working meal and promo tests; outputs carry rule IDs and versions | new (replaces the calculator) |
| `ledgers.py` | — | Per-person yearly ledgers, allowance optimizer (POL-BV-OPT), corrections against what was booked (§5.6) | new (adapts the schedule idea) |
| `schedule.py` | AP prepaid/accrual/terms schedule | Replaced by per-person ledgers | retire |
| `fx.py` | Period FX rates | One entity, EUR only | retire |
| `rounding.py` | Half-up cents, splits with leftover cent | Same | keep |
| `__init__.py` | Docstring | Same | keep |

## Layer 4, detectors and pre-pass: `vouch/recon/` (still payroll code at v3 start)

| File | Today | v3 | Change |
|---|---|---|---|
| `checks.py` | Payroll pairwise checks | Event tax detectors (§6.3) | replace |
| `findings.py` | Payroll finding structure | Finding: flag, kind, rule refs, evidence, amount, request | adapt |
| `sources.py` | Reads payroll sources | Reads visible v3 rows; derives statuses and what was booked | adapt |
| `run.py` | Payroll reconciliation | Deterministic pre-pass per trigger (D21): match, classify, four regimes, ledgers, detectors, routing, proposal | adapt |
| `rulings.py` | Payroll precedents | Memory: learned corrections from tax manager answers | adapt |
| `materiality.py` | FIN-01 thresholds | Thresholds live in routing (POL-MAT) | retire |
| `__main__.py`, `__init__.py` | CLI, docstring | Pre-pass for one scenario trigger | adapt |

## Layer 5, controls: `vouch/controls/` (payroll roles at v3 start)

| File | Today | v3 | Change |
|---|---|---|---|
| `audit.py` | Hash-chained append-only audit trail | Same, plus rule IDs/versions and inputs per decision | keep |
| `clock.py` | Simulated clock from the payroll close | Starts at the scenario's `as_of` | adapt |
| `gates.py` | Payroll role permissions | Roles tax_manager, controller, payroll, cfo, organizer, vouch; no path changes elections, files returns, runs payroll or writes closed periods (D25) | adapt |
| `approvals.py` | Requests, decide, follow-up | Same engine; needs come from routing; delegation; reminder after 24 business hours | adapt |
| `matrix.py` | — | Routing POL-MAT (§5.9) and delegation | new |
| `schema.sql` | Audit, cases, holds, requests | Same, plus decisions | adapt |
| `__main__.py`, `__init__.py` | Inbox, decide, verify | Same | keep |

## Layer 6, agent: `vouch/agent/` (payroll at v3 start, except two AP files)

| File | Today | v3 | Change |
|---|---|---|---|
| `runner.py` | Payroll monthly close | Trigger runner: process_invoice, process_event, process_email, process_attendance, run_period_close, run_year_end | adapt |
| `tools.py` | Payroll tools | §7 tools; no tool approves, files, runs payroll, changes an election | replace |
| `policy.py` | ModelPolicy (Claude) and ScriptedPolicy | Same two; Claude Opus 5.5; speed flags | adapt |
| `claims.py` | Claim check | Same; v3 identifier and rule patterns | adapt |
| `untrusted.py` | AP injection and bank-change patterns | Injection patterns for invoices, emails and replies; election-change requests | adapt |
| `questions.py` | Payroll questions | `request_info` to organizer / vendor / payroll | adapt |
| `colleagues.py` | Simulated payroll colleagues | Scripted `people_responses`; model-played only with `--live-people` (D22) | adapt |
| `defend.py` | Defends a case from its audit trail | Same | keep |
| `templates.py` | AP entry templates | Event tax entries (§6.4) | replace |
| `schema.sql` | Questions, agent runs | Requests, agent runs with perf counters, decisions | adapt |

## Layer 7, evaluator: `vouch/evaluator/` (payroll at v3 start)

| File | Today | v3 | Change |
|---|---|---|---|
| `answer_key.py` | Sealed payroll key | Loads and strips `expect` blocks | adapt |
| `match.py` | Matches findings to plants | Compares a decision with its `expect` block (§8.1) | adapt |
| `score.py` | Five payroll measures | Pass/fail per case, catch rate, false positives, amount accuracy, safety | adapt |
| `run.py` | Payroll sets | Public or holdout scenarios, concurrency, reports (holdout aggregates only) | adapt |
| `bench.py` | — | Bench per case: time, LLM calls, tokens, cache tokens, tool time, cost | new |
| `table.py` | README results table | Same, v3 columns | adapt |
| `__main__.py`, `__init__.py` | score, table | score [--holdout] [--reveal], bench, table | adapt |

## Layer 8, API: `vouch/ui/` (payroll dashboard at v3 start)

| File | Today | v3 | Change |
|---|---|---|---|
| `server.py` | Dashboard API for the payroll UI | REST + SSE API of §10 for the React frontend (CORS for localhost:5173) | replace |
| `jobs.py` | Background jobs | Run registry with streamed steps | adapt |
| `static/*` | Payroll dashboard frontend | The frontend is built separately in React | retire |

## Tests: `tests/`

| File | Today | v3 | Change |
|---|---|---|---|
| `conftest.py` | Base/scenario DB fixtures | Same | keep |
| `test_t2_loader.py` | AP loader tests | v3 loader, fixtures, generator, time travel | adapt |
| `test_b2_chart.py`, `test_b3_journal.py`, `test_b4_subledgers.py`, `test_b5_period_close.py` | AP ledger tests | Same subjects, v3 chart and close day | adapt |
| `test_t3_matching.py`, `test_t4_entries.py`, `test_t4_currency.py` | AP engine, entries, FX | Replaced by event tax engine, ledgers and entries tests | retire |
| `test_t5_rounding.py` | Splits | Same | keep |
| `test_c1..c4_*.py`, `test_g*_*.py`, `test_e1_score.py`, `test_e4_results_table.py`, `test_k5_no_hardcoding.py`, `test_u_dashboard.py` | Payroll-era controls, agent, scorer, dashboard | Same subjects for v3 | adapt |

## Additional files and final integration

| File | Role | Change |
|---|---|---|
| nergis_DOC.md | Per-task plain-language and software/finance reports, verification and limitations | new |
| vouch/controls/matrix.py | POL-MAT routing and valid delegation lookup | new |
| vouch/agent/openai_backend.py | Optional GPT Responses adapter; same agent loop contract and controls | new |
| vouch/evaluator/bench.py | Actual per-case time/token/cache/tool/cost baseline | new |
| tests/test_live_agent.py | Real Claude public suite and five-run P11 tests; no silent skip | new |
| tests/test_openai_backend.py | Responses protocol, reasoning preservation, usage and safe-failure checks | new |
| tests/test_v3_controls.py | Event-tax routing, role boundaries, balanced templates and duplicate checks | new |
| tests/test_v3_edge_cases.py | Generalized year, cap, trigger, reply, delegation, recovery and year-end regressions | new |
| frontend/index.html, app.js, styles.css | Imported origin/frontend design connected to event-tax API and ten-year Books | import/adapt |
| prompt_audit.json | Actual deterministic prompt/schema contract audit; scope labelled | new |
| PERF.md, perf_baseline.json | Measured results and remaining owner-holdout gate | new |

The obsolete payroll tests were adapted with the user's explicit approval. Existing T2/T3/T4 and
bookkeeping tests were preserved. Later regression failures were repaired in production code without
loosening their assertions. CLAUDE.md records the user's single-agent constraint (D26).
