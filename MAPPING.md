# MAPPING: payroll (v1) to event vendor payables (v2)

Every file in v1 (as tagged at `payroll-legacy`), what it does today, and what it becomes in v2.
Change types: **keep** (unchanged or only the domain words change), **adapt** (same role and pattern,
new domain logic), **replace** (same place and role, new contents), **retire** (removed from the main
path; still in the tag `payroll-legacy`), **new** (a file v2 adds inside an existing layer).

The layers stay the same and in the same order. Each layer reads only from the layers above it:
company world (fixtures and scenarios) → system of record (db) → calculations (calc) → detectors
(recon) → controls → agent → evaluator → UI.

## Top level

| File | v1 today | v2 | Change |
|---|---|---|---|
| `CLAUDE.md` | Project rules for payroll/equity | Event AP description, glossary, chart of accounts, approval matrix, source-of-truth table, decisions D18–D24 (spec §12) | adapt |
| `Vouch — Build Specification.md` | v1 build spec | Replaced by `VOUCH_EVENT_AP_SPEC.md` | retire |
| `VOUCH_EVENT_AP_SPEC.md` | — | The v2 spec | new |
| `MAPPING.md` | — | This checklist | new |
| `PLAN.md` | — | Task plan T1–T12 with status | new |
| `PERF.md`, `perf_baseline.json` | — | Speed measurements before/after (spec §9) | new |
| `README.md` | Payroll seats line, commands, results table | Seat line (verbatim §1), commands, real vs simulated, controls, generated results table | adapt |
| `FIX_LOG.md` | Every fix with root cause | Same; v2 fixes appended | keep |
| `pyproject.toml` | Package metadata, deps | Description changes; adds `pyyaml` (scenario format is YAML) | adapt |
| `.gitignore` | Generated world, db, sealed key | Adds per-case temp DBs; `/world/` and `/sealed/` no longer produced | adapt |
| `scripts/register_daily_fx.ps1` | Daily FX refresh task | Rates are frozen period rates (spec §4.4) | retire |
| `heldout/README.md` | Held-out plant mechanics (in repo) | Holdout lives outside the repo at `VOUCH_HOLDOUT_PATH` (D23) | retire |
| `results/*.json` (payroll) | Payroll score reports | Kept in the tag; v2 reports start fresh | retire |
| `cases/public/*.yaml` | — | Public planted cases P01–P15 and clean cases C01–C04 (spec §8.2) | new |

## `vouch/` (package root)

| File | v1 today | v2 | Change |
|---|---|---|---|
| `__init__.py` | Package docstring | Event AP docstring | adapt |
| `__main__.py` | `rebuild`, `close`, `colleagues`, `audit`, `why`, `score`, `ui` | `rebuild`, `run` (one scenario's triggers), `why`, `score`, `bench`, `ui` | adapt |
| `money.py` | Exact decimal helpers, half-up cents | Same | keep |
| `dates.py` | Month helpers | Same, plus day arithmetic for terms and tiers | keep |

## Layer 1, company world: `vouch/world/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `build.py` | Generates the payroll world from a seed | Builds the base company (Appendix A fixtures) into a database; one command | adapt |
| `fixtures.yaml` | — | Appendix A, seeded exactly | new |
| `scenario.py` | — | Scenario loader: base fixtures + `setup` + `overrides`, time travel to `as_of`, strips `expect` | new |
| `__main__.py` | `python -m vouch.world build` | Same command; builds the base company | adapt |
| `config.py` | Seed, company shape, salary bands | Replaced by fixtures | retire |
| `payroll.py` | Generates registers through the pay engine | No payroll | retire |
| `simulate.py` | Hires, raises, exits, transfers | No people simulation; events/vendors are fixtures | retire |
| `plants.py` | Planted payroll mistakes and castings | Planted cases are scenario YAML in `cases/public/` | retire |
| `noise.py` | Typos, duplicates, timing noise | Scenarios carry their own messy data | retire |
| `banking.py` | Payment files and bank statements | `payments` table rows in fixtures/scenarios | retire |
| `documents.py` | Policies, inbox, standing documents | `policies`, `emails` tables | retire |
| `files.py` | CSV/JSON writers, manifest | Not needed (no file world) | retire |
| `answer_key.py` | Writes the sealed key | The key is each scenario's `expect` block, stripped before the agent runs | retire |
| `heldout.py` | Loads held-out plant modules | Holdout loader moves to the evaluator (`VOUCH_HOLDOUT_PATH`) | retire |
| `policies.py` | Pay and finance policy text | `policies` table in fixtures | retire |

## Layer 2, system of record: `vouch/db/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `__init__.py` | connect/create, schema list | Same | keep |
| `schema.sql` | Documents and source rows as loaded, payroll views | Event AP tables (§4.1): entities, cost centers, people, delegations, events, vendors, bank accounts, master changes, contracts, clauses, POs, invoices, evidence, emails, payments, policies, period rates, vendor history | replace |
| `ledger.sql` | Journal entries/lines, line sources | Same tables; lines carry cost center and event tags, entries carry type, source invoice and reversal link | adapt |
| `periods.sql` | Lock triggers, controller-only status change | Same triggers; periods carry `close_date` (5th of M+1), status derived from `as_of` | adapt |
| `accounts.py` | Payroll chart of accounts | Event AP chart (§4.3) | replace |
| `ledger.py` | Entry/Line, balance check, refusals | Same balance check; expense lines need cost center + event | adapt |
| `periods.py` | Lock/reopen, corrections in the open month | Same; closed = past its close date at `as_of`; late items post in the current period | adapt |
| `load.py` | Loads world files with hashes | Loads fixture and scenario rows into tables | replace |
| `rebuild.py` | Rebuild DB from world | Rebuild base company DB from fixtures | adapt |
| `rows.py` | Required fields for payroll rows | Required fields for invoices/lines/evidence (malformed → hold) | adapt |
| `subledger.py` | Payroll and equity subledgers roll up to GL | AP subledger (open items per vendor) and prepaid subledger (per event) roll up to GL | adapt |
| `__main__.py` | Load summary | Same | keep |

## Layer 3, calculations: `vouch/calc/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `pay.py` | US/UK/DE pay function | Replaced by the matching engine | retire |
| `matching.py` | — | Matching engine (§6.1): expected amount per invoice line from clauses + evidence; approved/held/dispute split | new (replaces `pay.py`) |
| `stock.py` | Straight-line vesting schedule | Schedule idea reused: prepaid release on event date, accrual + auto-reversal, pay-by and discount dates | retire → `schedule.py` |
| `schedule.py` | — | Release, accrual/reversal, payment proposal (§5.9) | new (adapts `stock.py`) |
| `rates.py`, `tax_rates.json` | Statutory payroll rates with sources | TAX-SF and VAT come from `policies` and clauses | retire |
| `fx.py` | Recorded daily OXR rates, month averages | `period_rates` (GBP/USD 1.27 frozen); missing → last known rate with a warning | adapt |
| `rounding.py` | Half-up cents, splits with leftover cent | Same | keep |
| `__init__.py` | — | Same | keep |

## Layer 4, detectors: `vouch/recon/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `checks.py` | Pairwise payroll checks | Event AP detectors (§6.2) | replace |
| `findings.py` | Finding: sources compared, trusted source, rules, amount | Same structure; flag, dispute/hold/info, clause and evidence refs | adapt |
| `materiality.py` | FIN-01 thresholds | POL-CL-03 reclass threshold ($10,000) | adapt |
| `rulings.py` | Past rulings reused by later closes | Memory: learned corrections and vendor history (enhanced review) | adapt |
| `sources.py` | Reads loaded payroll sources | Reads visible event AP rows | adapt |
| `run.py` | Runs reconciliation, stores findings | Deterministic pre-pass per trigger (D21): match, detectors, routing, proposal | adapt |
| `__main__.py` | `python -m vouch.recon run` | Pre-pass for one scenario trigger | adapt |

## Layer 5, controls: `vouch/controls/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `audit.py` | Hash-chained append-only audit trail | Same, plus rationales and citations | keep |
| `clock.py` | Simulated clock from the close date | Simulated clock starts at the scenario's `as_of` | adapt |
| `gates.py` | Role permissions, holds, drafts, posting, payments, bank change | Same engine; roles event_owner, controller, vp_finance, event_ops, vendor_master_admin; no path edits vendor master data (D20) | adapt |
| `approvals.py` | Requests, decide, follow-up, precedents | Same engine; `needs` from the approval matrix; delegation; reminder after 24 business hours | adapt |
| `matrix.py` | — | Approval matrix POL-AP-01/05/07, SoD, delegation, close entries POL-CL-04 | new |
| `schema.sql` | Audit, cases, holds, requests, releases | Same, plus disputes, payment proposals, decisions | adapt |
| `__main__.py` | Inbox, decide, verify | Same | keep |
| `__init__.py` | Docstring | Same | keep |

## Layer 6, agent: `vouch/agent/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `runner.py` | Fixed monthly close checklist | Trigger runner: process_invoice, process_email, run_period_close, review_prepaids | adapt |
| `tools.py` | Payroll read/compute/act tools | §7 tool list: get_case_file etc.; no approve/release/vendor-edit tool | replace |
| `policy.py` | ModelPolicy (Claude) and ScriptedPolicy | Same two; Claude Opus 5.5; speed flags (prepass, parallel, caching, effort) | adapt |
| `claims.py` | Claim check on numbers, dates, rules, names | Same; v2 identifier patterns | adapt |
| `untrusted.py` | Instruction words in emails/notes | Injection and bank-change patterns in invoices and emails | adapt |
| `questions.py` | Questions to colleagues | `request_info` to vendor / owner / ops | adapt |
| `colleagues.py` | Simulated colleagues (model or scripted) | Scripted `people_responses`; model-played only with `--live-people` (D22) | adapt |
| `defend.py` | Defends a case from its audit trail | Same | keep |
| `templates.py` | Payroll and stock entries | Deposit, final AP, release, accrual + reversal, reclass, discount entries | adapt |
| `schema.sql` | Questions, agent runs | Info requests, agent runs (with perf counters), decisions | adapt |

## Layer 7, evaluator: `vouch/evaluator/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `answer_key.py` | Loads the sealed key | Loads and strips `expect` blocks from scenarios | adapt |
| `match.py` | Matches findings to plants | Compares a decision to its `expect` block (§8.1 rules) | adapt |
| `score.py` | Five measures per run | Per-case pass/fail, catch rate, false positives, amount accuracy, safety violations | adapt |
| `run.py` | Builds sets, runs closes, reports | Runs public or holdout scenarios, concurrency, reports (holdout: aggregates only) | adapt |
| `bench.py` | — | `bench`: time, LLM calls, tokens, cache tokens, tool time, cost per case | new |
| `table.py` | README results table | Same, v2 columns | adapt |
| `__main__.py` | `score`, `table` | `score [--holdout] [--reveal]`, `bench`, `table` | adapt |

## `vouch/reference/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `fetch_fx.py`, `fx_oxr_usd.csv` | Daily OXR snapshot | Frozen period rates in fixtures | retire |
| `__init__.py` | — | Removed with the folder | retire |

## Layer 8, UI: `vouch/ui/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `server.py` | Overview, audit, cases, inbox, ledger, results, jobs | Same views; adds invoice match view, disputes and holds, summary header | adapt |
| `jobs.py` | Background rebuild/close/score | Background rebuild/run/score | adapt |
| `static/index.html`, `app.js`, `app.css` | Dashboard | Same framework, v2 views | adapt |

## Tests: `tests/`

| File | v1 today | v2 | Change |
|---|---|---|---|
| `conftest.py` | World/db fixtures | Base company and scenario fixtures | adapt |
| `test_w1..w6_*.py`, `test_casting.py`, `worlddiff.py` | Generator, plants, key, held-out mechanics | Generator retired | retire |
| `test_t2_payslips.py`, `test_t3_stock_comp.py` | Pay and vesting | Replaced by matching/schedule tests | retire |
| `test_t4_currency.py` | FX policy | FX period rates and thresholds | adapt |
| `test_t5_rounding.py` | Splits | Same | keep |
| `test_b1..b5_*.py` | Load, chart, journal, subledgers, period close | Same subjects, v2 data | adapt |
| `test_k1..k4, k6_*.py` | Payroll reconciliation | Replaced by detector tests | retire |
| `test_k5_no_hardcoding.py` | No code names a planted subject | Same scan for invoices, vendors, cases | adapt |
| `test_c1..c4_*.py` | Gates, inbox, busy approvers, audit trail | Same, v2 roles and matrix | adapt |
| `test_g1..g8_*.py` | Close, tools, ask, defend, claim check, recover, untrusted, colleagues | Same subjects for the trigger runner | adapt |
| `test_e1..e4, e6_*.py` | Score, sets, consistency, table, hard cases | Scorer, holdout aggregates, table | adapt |
| `test_u_dashboard.py` | Dashboard API | Same, v2 views | adapt |
