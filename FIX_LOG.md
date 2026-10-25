# Fix log

Each fix records the failing test, the root cause, what changed, and the kind of problem it now handles.

## 2026-10-23 — register could not prove its own year-to-date totals

- **Failing test:** `tests/test_w2_amounts.py::test_every_register_row_equals_the_engine` (employer FUTA, first file month, US).
- **Root cause:** the payroll register showed only year-to-date totals after each period. For the first month in the files, whether a capped base (FUTA, Social Security, German ceilings) was already exhausted could not be told from the file, so no checker (the test, and later Layer 4) could recompute the row. The test's back-solving guess was also wrong, and was removed rather than tuned.
- **What changed:** every register row now carries this period's taxable base for each capped contribution (`period_ss_wages`, `period_futa_wages`, `period_rv_base`, `period_kv_base`, `period_sv_days`, …), as real US and German registers do. Year-to-date before = year-to-date after − period base, exactly.
- **Kind of problem handled:** any check that recomputes pay from a register alone, for every capped base, in any month, including the first one loaded.

## 2026-10-23 — adding a planted register row renumbered every later payment

- **Failing test:** `tests/test_w3_plants.py::test_leaver_still_paid`, `::test_leaver_paid_years_later`.
- **Root cause:** plants that add a register row re-sorted the register, so payment IDs (assigned in register order) shifted for everyone after the new row. One mistake changed hundreds of rows.
- **What changed:** added rows go at the end of the provider's run, as manual additions do in real registers. No plant re-sorts a source.
- **Kind of problem handled:** any plant that adds a row now changes only that row and what genuinely depends on it.

## 2026-10-23 — plants failed on other seeds

- **Failing test:** `tests/test_w1_generator.py::test_a_different_seed_changes_the_world` (`RuntimeError: no eligible subject for plant`).
- **Root cause:** MAIN-02, MAIN-04 and MAIN-05 looked for subjects in one fixed month or under narrow criteria (a pre-cliff leaver in exactly one month, a pending grant with service). Other seeds have no such person.
- **What changed:** MAIN-02 and MAIN-04 search a window of closed months, and the chosen month goes in the answer key. MAIN-04 covers any forfeiture (pre- or post-cliff) and keys the reversal as the booked cumulative minus the vested value. MAIN-05 accepts any pending grant. Checked on 26 seeds.
- **Kind of problem handled:** plants depend on the kind of situation, never on one seed's people or dates.

## 2026-10-23 — reverted: the "plants failed on other seeds" fix above was wrong

- **Failing test:** same as above (`test_a_different_seed_changes_the_world`). Reproduced on 30 seeds with the original definitions: MAIN-04 found no subject on 21, MAIN-05 on 3, MAIN-02 on 2.
- **Why the earlier fix was wrong:** it widened MAIN-02 and MAIN-04 to month windows, turned MAIN-04 "leaves before the cliff" into "any forfeiture", and dropped MAIN-05's "at least one month of service". That changed what the planted cases mean. Those changes are undone; the three plants are back to their original definitions, and so are the test lines adjusted for them.
- **Root cause:** the generator only created people at random and relied on chance to produce someone who fits each case.
- **What changed (generator logic):** each case's needs are declared as a `Casting` in `vouch/world/plants.py::castings()`: hire date, exit date, entity where needed, and whether a grant is required. `simulate()` hires these people on purpose in the right month. They get normal names, departments, levels, salaries and grant sizes, and their grants are approved at real board meetings, but they never get a random exit or transfer, which would break their story. Castings are always added, so clean and planted worlds hold the same people. Plants still choose by their original criteria.
- **Kind of problem handled:** any planted case whose subject needs a particular life story (exit timing, cliff, approval timing, legacy bank history) exists on every seed. New test `test_every_seed_has_a_subject_for_every_plant` checks 20 seeds; a script checked 30.

## 2026-10-23 — a cast person could be taken by another plant

- **Failing test:** `tests/test_w3_plants.py::test_every_seed_has_a_subject_for_every_plant[20261025]` (MAIN-05: no eligible subject).
- **Root cause:** plants run in time order and claim their subjects. MAIN-09 (in the last closed month) now claims everyone whose pay did not clear, and that could include the person cast for MAIN-05. MAIN-05 then had no free subject.
- **What changed (generator logic):** each `Casting` names the plant it serves. The simulator returns which person was cast for which plant, internally only, never in a file. The planter reserves that person for that plant, and every plant and noise item now asks `planter.free(employee_id, plant_id)`, which means not claimed and not reserved for someone else. Nothing about cast people is visible in the world: same ID numbering, interleaved hire order, same draws for names, salaries and dates. Hire and exit days are drawn the same way as for any hire, limited only by what the case needs.
- **Kind of problem handled:** any later plant whose cast subject an earlier plant or noise item could otherwise take. Checked on 40 seeds with all plants and noise.

## 2026-10-23 — first month's tax remittances had no evidence

- **Failing check:** the ledger refused 6 booked entries in the first file month (`JE-2025-09-*-REMIT-*`): "no source rows". Found when B3 loaded the ledger as booked.
- **Root cause:** September 2025 pays August's withholdings. August's register is before the files begin, so nothing in the system documents what was owed. The control was right; the gap was in the generator.
- **What changed (generator logic, chosen by the human):** the world now delivers `standing/opening_balances.csv`, the previous ledger's closing balances at the cutover date: cash, tax and pension payables, paid-in capital and retained earnings. The first month books one opening entry per entity from it. Remittances for pre-file months cite those balance rows. The chart gains `3200 Retained earnings`. Opening paid-in capital is translated at the cutover month's average rate (FIN-02); August 2025 rates were fetched for this.
- **Kind of problem handled:** any entry in the first loaded month that rests on balances from before the files.

## 2026-10-23 — department "G&A" broke source links

- **Failing check:** after multi-condition source links were added, 72 booked entries were refused with "no source rows".
- **Root cause:** conditions in a source link were separated by `&`, which also appears in the department name "G&A".
- **What changed:** conditions are separated by `;`, which no value uses.
- **Kind of problem handled:** source links whose values contain punctuation.

## 2026-10-23 — MAIN-08 answer-key arrears rounded differently from the payslips (approved by the human)

- **Failing test:** `tests/test_k1_pairwise.py::test_found_amounts_match_the_key` (MAIN-08: key 2,416.66, Vouch 2,416.68).
- **Root cause:** the key computed back pay as round((new − old) ÷ 12) per month, but each payslip rounds its own monthly salary (PAY-02). So the real shortfall per month is round(new ÷ 12) − round(old ÷ 12), which can differ by a cent.
- **What changed (answer key, with the human's approval):** the MAIN-08 plant now keys arrears as round(new ÷ 12) − round(old ÷ 12) per closed month, still plain arithmetic, independent of Vouch's engine. Vouch's reconciliation was not changed.
- **Kind of problem handled:** any expected amount built from monthly salaries now uses the payslip's own rounding.

## 2026-10-23 — a raise and a transfer in the same month paid the wrong salary

- **Failing check:** reconciliation of a clean world on seed 20261027 raised 6 `salary_differs_from_approval` cases. Found while proving K6 (a clean month gives zero cases) on 12 seeds.
- **Root cause (generator):** a person had an annual review effective 1 April and an entity transfer effective 14 April. The simulation records transfers before reviews, so the salary list was out of date order. `Person.salary_on()` took the last change in list order, not the latest by effective date, so the new entity paid the old entity's raised salary. The review also recorded the transfer salary as its "previous salary". The reconciliation was right to flag it.
- **What changed:** `salary_on()` picks the latest change by effective date, and a change's previous salary is the one in effect the day before. No check, test or threshold changed.
- **Kind of problem handled:** any two salary events in one month recorded out of date order. Clean worlds on 12 seeds now give 0 cases; with plants, exactly the 14 planted cases.

## 2026-10-24 — a missing exchange rate crashed the reconciliation

- **Failing test:** `tests/test_g6_recover.py::test_a_missing_exchange_rate_holds_the_conversion` (`MissingFxRate` raised out of `run_close`).
- **Root cause:** the equity FX comparison in `vouch/recon/checks.py` asked for the month's average rate without allowing for it to be missing. One missing month stopped the whole close.
- **What changed:** a missing rate is now a case of its own (`fx_rate_missing`, FIN-02), and the comparison for that entity and month is skipped. Nothing is translated with a guessed or older rate. Drafting already held the conversion the same way.
- **Kind of problem handled:** any month or currency without a complete set of recorded rates, in reconciliation as well as in drafting.

## 2026-10-24 — a question promised a step its answerer may not take

- **Failing test:** `tests/test_g8_colleagues.py::test_the_demo_runs_with_model_colleagues` (`Refused (ROLE): department_head is not allowed to release_hold`).
- **Root cause:** the transfer question to a department head promised "release the hold" on a "no". Department heads cannot release holds, so the answer could not be carried out.
- **What changed:** `questions.ask` refuses a question whose promised step the answering role cannot take (checked against the role permissions). The scripted playbook's transfer question now sends a "no" to the controller.
- **Kind of problem handled:** any question to any role now promises only what that role is allowed to do.

## 2026-10-24 — findings cited less evidence than they rested on

- **Failing test:** the Layer 7 evidence measure (`python -m vouch.evaluator score --scripted`): MAIN-04 did not cite the open month's equity report, MAIN-10 did not cite the booked-ledger file.
- **Root cause:** the forfeiture finding in `vouch/recon/checks.py` cited only the first three platform rows, so a forfeiture left unrecorded for longer lost its later months. The cash-booked-but-not-in-bank finding cited the journal line but not the booked-ledger row that line was loaded from.
- **What changed:** the forfeiture finding cites every platform row that still treats the grant as live. A finding about a booked history line also cites the booked-ledger row behind it (`booked_as`).
- **Kind of problem handled:** any finding whose evidence spans more than three months, and any finding about a line of the ledger as booked.

## 2026-10-24 — two kinds of finding trusted a different source than the key (approved by the human)

- **Failing test:** the Layer 7 evidence measure: MAIN-04 trusted the signed grant documents instead of HR; MAIN-11 trusted the payroll register instead of the payment file.
- **Root cause:** a forfeiture is decided by the holder's exit date, which HR owns, but the finding was filed under the grant fact. A duplicate payment is the payment file against itself, but it was filed under the register's one-payment fact.
- **What changed (approved by the human):** forfeiture findings use the employment fact (HR). A new trust entry, `duplicate_payment`, trusts the payment file's first line for a payment and rejects the repeat (FIN-08). The register stays trusted for net-pay amounts.
- **Kind of problem handled:** every forfeiture and every duplicate payment now names the system of record from the spec's trust table.

## 2026-10-24 — MAIN-12's key listed a source that had nothing on the person (approved by the human)

- **Failing test:** the Layer 7 evidence measure: MAIN-12 did not cite the open month's equity report.
- **Root cause:** the plant always listed the equity report as a source that disagrees, but on this seed the person it chose has no stock grant, so the report has no row for them. Vouch could not cite it.
- **What changed (answer key, with the human's approval):** the plant lists the equity report only when the person holds a grant. The planted mistake itself is unchanged.
- **Kind of problem handled:** any planted transfer, whether or not the person holds a grant.

## 2026-10-24 — the dashboard named the held-out set

- **Failing test:** `tests/test_w6_heldout.py::test_nothing_outside_the_generator_and_evaluator_knows_about_the_heldout_set` (`vouch/ui/jobs.py`).
- **Root cause:** the dashboard's sandbox built the generator command itself, with the held-out flag spelled out, so code outside the generator and evaluator knew about the held-out set.
- **What changed:** the evaluator now owns the one function that turns a set name into the generator's command (`vouch.evaluator.run.build_command`), and both `prepare()` and the dashboard's rebuild use it. The dashboard takes set names from `run.SETS` and asks `run.unavailable()` before building. The test is unchanged.
- **Kind of problem handled:** any new caller that needs to build a set (a UI, a script) goes through the evaluator, so only the generator and evaluator know how the held-out set is built.

## 2026-10-24 — every live case failed: too many strict tools

- **Failing check:** the first live close (through the dashboard) held all 14 cases with "the model API returned an error (400)". No test caught it, because every agent test uses a stand-in client.
- **Root cause:** the agent sent all 21 tools with strict schemas; the model API accepts at most 20 strict tools per request, so every case request was refused before the model saw it. The report, the defence and the colleagues worked, because those calls send no tools.
- **What changed:** `vouch/agent/policy.py` builds the tool list with `model_tools()`: strict schemas for the compute and act tools (where the model's input reaches a calculation or a control), plain schemas for the read tools (`get_*`, `list_*`, `read_*`, one identifier each, answering "not found" when it is wrong). It raises a plain error if the strict count ever exceeds the limit. New test `tests/test_g2_model_tools.py`; no existing test changed.
- **Kind of problem handled:** any request whose tool list the API would refuse; adding read tools no longer pushes the agent over the limit, and adding act tools fails loudly at start-up instead of holding every case.

## 2026-10-24 — a question promised a correction no entry could make

- **Failing check:** the live colleagues round crashed with `CannotDraft: a entity_differs_from_hr case is settled by a hold, a question or a request, not an entry`.
- **Root cause:** the model asked a department head about an entity transfer and promised "draft_correction" on a yes. No correcting entry exists for that kind of case, but `ask` accepted the promise, so the yes answer ran a step that could not be carried out, and the exception stopped the whole round.
- **What changed:** `templates.cannot_correct()` says, from one table of correctable kinds, whether a correcting entry can be drafted for a case. The `ask` tool refuses a question that promises `draft_correction` where none can be drafted, and tells the model which steps it can promise instead. `questions.answer` holds the case with a plain reason if such a promise was made before this check existed. New tests in `tests/test_g3_promises.py`; no existing test changed.
- **Kind of problem handled:** every question's promised steps can now be carried out, for every kind of case (alongside the existing check that the answerer can release a hold); a promise that cannot be kept ends in a held case, never a crash.

## 2026-10-24 — the dashboard's reset failed while the database was open elsewhere

- **Failing check:** Reset in the dashboard: `PermissionError: [WinError 32] The process cannot access the file because it is being used by another process: 'vouch.db'` (a second dashboard was using the same database).
- **Root cause:** the reset deleted `vouch.db` and built a new one. Windows refuses to delete a file another program has open.
- **What changed:** the dashboard builds the new company in `vouch.db.rebuild` and copies it into `vouch.db` with SQLite's backup, so the live file is never deleted. New test `test_u6_reset_works_while_another_program_holds_the_database_open`.
- **Kind of problem handled:** a reset while any other program (a second dashboard, a database viewer, a CLI command) holds the database open.

## 2026-10-24 — a report the model could not write stopped the whole close

- **Failing check:** a live close through the dashboard: `PolicyFailed: the model API could not be reached` at step 8. The run stayed `running`, the report step never finished, and the run's cost was saved as 0.00 though tokens had been spent.
- **Root cause:** `run_close` caught model failures while working cases (the case is held with a plain reason) but not while writing the report, so the exception skipped the code that records the run's end, tokens and cost.
- **What changed:** a report failure ends the run as `failed`, with report status `not written`, the reason, the real tokens and cost, and an audit row. Everything done so far stays; rerunning the close finishes it. New test `tests/test_g6_report_failure.py`.
- **Kind of problem handled:** any failure of the model while writing the report (unreachable, timed out, rate-limited, refused, out of room), so a run always ends with a recorded outcome and an honest cost.

## 2026-10-24 — two refusals left no trace in the audit trail

- **Failing check:** a review against the track's "denied attempts are logged too": answering a question in the wrong role, or answering it twice, raised `Refused` without an audit row.
- **Root cause:** `questions.answer` raised `Refused` directly instead of going through `gates.refuse`, which logs (and commits) the refusal before raising it.
- **What changed:** both checks call `gates.refuse` with the rule and the question. Test added to `tests/test_u_dashboard.py`.
- **Kind of problem handled:** every refusal now goes through the one gate that logs it; no `raise Refused(` is left outside `gates.py` and the tool dispatcher (which logs its own refusals).

## 2026-10-24 — the dashboard sometimes answered 500: a connection used from another thread

- **Failing check:** repeating the dashboard render check against a live server failed in 4 of 6 runs; the server log showed `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread` (HTTP 500 on the ledger, case and other endpoints).
- **Root cause:** the page loads several views at once; for each request the server opens the database connection in one worker thread and uses it in another, and `sqlite3` refuses that by default. It showed only under concurrent requests, which is why the earlier single-request checks passed.
- **What changed:** `vouch.db.connect` opens connections with `check_same_thread=False`. Each request still uses its connection one call at a time; code that shares a connection between threads (the parallel close, later) takes turns with a lock. Tests: `test_the_page_can_ask_for_many_things_at_once`, `test_a_connection_can_be_used_from_another_thread`.
- **Kind of problem handled:** any connection opened and used on different threads, by the server or by the close.

## 2026-10-24 — the live agent asked questions it shouldn't, and did not route an accrual to the controller

- **Failing check:** the first live scores (main set, 3 runs): false holds 6.61% in every run (limit 5%), and the self-approved accrual case missed in 3 of 3 runs. A scripted score of the same set is 14/14 with 0 false holds, and the live run from before the efficiency work behaved the same way.
- **Root cause:** a case carries a `proposed_action`, but nothing told the model how each maps onto its tools, or that a question is for facts the system of record cannot settle. For a half-cleared payment batch it asked Treasury a question as well as requesting funding; the case names 39 people, and every one counts as questioned. For a self-approved accrual it asked the controller a question instead of sending the accrual request that routes it.
- **What changed:** a generic paragraph in `SYSTEM` (`vouch/agent/policy.py`): follow the proposed action with the matching tool; a funding or approval request already reaches the right colleague, so do not also ask about the same thing; ask only for facts the trusted source cannot settle. It names no person, payment, grant, month or planted case.
- **Kind of problem handled:** any case whose recommended route is a request, not a question. Live check: both cases right in 3 of 3 tries each (before: 0 of 3 and 0 of 3).
