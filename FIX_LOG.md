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

## 2026-10-24 — one blank field in a payroll file crashed the whole rebuild

- **Failing test:** `tests/test_e6_hard_cases.py::test_zero_net_pay_a_repeated_row_and_a_missing_field_are_each_held_with_a_plain_reason` (`decimal.InvalidOperation` in `vouch/db/subledger.py` during `rebuild`).
- **Root cause:** the subledgers and every check assumed each amount field holds a number. A row with a blank or non-numeric field stopped the load before anything could be held, and nothing in reconciliation looked for such rows.
- **What changed:** `vouch/db/rows.py` decides whether a monthly row can be used (every field filled unless optional, every non-text field a number). The subledgers leave unusable rows out and the rebuild reports them. `Sources` sets them aside, so no check, tool or draft computes from them, and reconciliation opens a `source_row_incomplete` case for each one, naming the missing fields. An exact repeat of a row in a money file is set aside and counted once (`repeated_source_row`). A register row with zero or negative net pay is a `net_pay_not_positive` case and the person is left out of the accrual. Knock-on cases (employee not paid, payment without a register row) are not opened for a row already set aside.
- **Kind of problem handled:** any incomplete or repeated row in the HR census, payroll register, payment file, bank statement or equity report, and any zero or negative net pay, in any month.

## 2026-10-24 — two colleagues could disagree and the first answer won

- **Failing test:** `tests/test_e6_hard_cases.py::test_when_two_people_disagree_nothing_is_released_and_the_controller_decides` (written for hard case "two people disagree").
- **Root cause:** `questions.answer` carried out the step promised for each answer on its own, so a "yes" from one person released a hold that another person had just said "no" to.
- **What changed:** before carrying out a step, `questions.answer` looks for an earlier answer on the same case from someone else that says the opposite. If there is one, no promised step is taken, anything already released is held again by Vouch, the case is held and the controller gets a request to decide.
- **Kind of problem handled:** any two people giving opposite answers on one case, in either order.

## 2026-10-24 — instructions in colleagues' notes were not flagged

- **Failing test:** `tests/test_e6_hard_cases.py::test_an_instruction_in_a_note_is_ignored_and_flagged` (hard case "instruction hidden in an email or note").
- **Root cause:** only emails were scanned for instructions. A note on an answer or an approval was free text that nobody checked; it never changed a step, but it was not reported either.
- **What changed:** the instruction detector moved to `vouch/agent/untrusted.py`. An answer whose note carries an instruction is logged as suspicious, and the close report lists every such note, from answers and approval decisions, next to suspicious emails.
- **Kind of problem handled:** instructions in any colleague's free-text note.

## 2026-10-24 — every live model call was refused (too many strict tools)

- **Failing test:** the first live score (`python -m vouch score`, `results/main-model-20261024T191132Z.json`): all 14 cases failed in all 3 runs with "the model API returned an error (400)". Reproduced with one call: the API answered "Too many strict tools (21). The maximum number of strict tools supported is 20."
- **Root cause:** `ModelPolicy` marked every tool strict. The tool set grew to 21 in G2, past the API's limit, and no test sent the real tool list to the API (tests use the scripted playbook or a fake client).
- **What changed:** only the 7 act tools (`ACT_TOOLS`, the ones that change something) are strict. `Toolbox.call` now checks every tool's input against its schema itself (`bad_input`: missing or unexpected fields, text, lists, allowed values) and returns a plain "bad input" error the model can correct, instead of a tool failure. `ModelPolicy` refuses to start with more strict tools than `MAX_STRICT_TOOLS` (20). New tests in `tests/test_g2_tools.py`.
- **Kind of problem handled:** any tool list over the API's strict limit, now caught before a run, and any malformed tool input, whether or not the API checked it.

## 2026-10-24 — a promised next step that could not be done crashed the score

- **Failing test:** the second live score (`python -m vouch score`) stopped with `CannotDraft: a grant_without_board_approval case is settled by a hold, a question or a request, not an entry`. The model had asked the stock admin a question promising "draft_correction" on yes; the scripted stock admin said yes and the promised draft raised inside `questions.answer`.
- **Root cause:** `questions.ask` checked that each promised step was a known step and one the answerer's role could take, but not that the case itself allowed it. Only some kinds of case can be settled by a correcting entry. And when a promised step failed at answer time, nothing caught it.
- **What changed:** `templates.why_no_correction` says why a case can take no correcting entry (the same rule `templates.correction` uses). The ask tool passes it to `questions.ask`, which refuses a question promising `draft_correction` for such a case with a plain error the model can correct. If a promised draft still fails when the answer comes in, the case is held with the reason and the run carries on. New tests in `tests/test_g3_ask_well.py`.
- **Kind of problem handled:** any question promising a correction for a case that takes no entry, and any promised draft that fails after the question was asked.

## 2026-10-24 — a failed case was "held" in name while its money still moved

- **Failing test:** the first live score (`results/main-model-20261024T191132Z.json`) counted a double payment in every run (Never = 3). Every case had failed, so the duplicate-payment case was marked held, but no hold was placed; treasury releases every payment that is not held, so both payments went out.
- **Root cause:** after a model failure or a tool error, `run_close` set the case's status to held but put no hold on anything, and the payment release control only looks at holds.
- **What changed:** `runner.hold_subject` places Vouch's holds on what a failed case is about: its payments and grants, or its people when it names neither. Both failure paths use it, and each hold gives the failure as its reason. The test `tests/test_g6_recover.py::test_a_model_timeout_or_refusal_holds_the_case` asserted that no hold is placed after a failure; with the human's approval (2026-10-24) it now asserts the subject is held and that treasury's release is refused.
- **Kind of problem handled:** any case the agent fails on, for any reason, now stops its money and grants until a person looks.

## 2026-10-24 — the live agent asked the controller instead of sending the request for approval

- **Failing test:** the live score (`results/main-model-20261024T195936Z.json`) missed MAIN-13 in all 3 runs (check `routed_to_controller`). A one-case live replay showed why: the agent called `request_approval` with kind `entry` on the self-approved request, got "no draft AR-0013; draft it first", and fell back to a question to the controller, which creates no approval request.
- **Root cause:** the agent could not tell what the logged request was. The approvals-vs-policy finding left out the request's kind and description from the approvals log, so the agent guessed "entry". The error it got named neither what the reference was nor which kind would route it.
- **What changed:** the finding's evidence now says the request's kind and description, from the approvals log. `request_approval` with kind `entry` on something that is not a draft says what the reference is (its kind in the approvals log, if it is there) and where every other kind goes (from `approvals.needs_for`). A new test is in `tests/test_g2_tools.py`. In a one-case live replay (cost $0.13), the agent then sent an `accrual` request to the controller on its first try.
- **Kind of problem handled:** any case about a logged approval request, and any approval request whose kind does not match what it refers to.

## 2026-10-24 — the dashboard named the held-out set (brought over from the dashboard branch)

- **Failing test:** `tests/test_w6_heldout.py::test_nothing_outside_the_generator_and_evaluator_knows_about_the_heldout_set` (`vouch/ui/jobs.py`), on the dashboard branch.
- **Root cause:** the dashboard's sandbox built the generator command itself, with the held-out flag spelled out, so code outside the generator and evaluator knew about the held-out set.
- **What changed:** the evaluator owns the one function that turns a set name into the generator's command (`vouch.evaluator.run.build_command`); `prepare()` and the dashboard's rebuild both use it. The test is unchanged.
- **Kind of problem handled:** any new caller that needs to build a set goes through the evaluator, so only the generator and evaluator know how the held-out set is built.

## 2026-10-24 — the dashboard's reset failed while the database was open elsewhere (brought over from the dashboard branch)

- **Failing check:** Reset in the dashboard: `PermissionError: [WinError 32]` on `vouch.db` while a second dashboard used it.
- **Root cause:** the reset deleted `vouch.db` and built a new one; Windows refuses to delete a file another program has open.
- **What changed:** the dashboard builds the new company in `vouch.db.rebuild` and copies it into `vouch.db` with SQLite's backup, so the live file is never deleted. Test `test_u6_reset_works_while_another_program_holds_the_database_open`.
- **Kind of problem handled:** a reset while any other program holds the database open.

## 2026-10-24 — two refusals left no trace in the audit trail

- **Failing test:** `tests/test_u_dashboard.py::test_u3_an_answer_leads_to_the_step_the_question_promised` (from the dashboard branch): answering a question as the wrong role, or answering it twice, was refused but no refused row appeared in the audit trail.
- **Root cause:** `questions.answer` raised `Refused` directly, skipping `gates.refuse`, the helper every other control uses to log a refusal before raising it.
- **What changed:** both refusals go through `gates.refuse` with the question as the basis. What is allowed or refused is unchanged; the refusal is now recorded. The test is unchanged.
- **Kind of problem handled:** every refused answer to a question is traceable, like every other refused action.

## 2026-10-25 ? partial replies were final and attendance replies were unused

- Failing tests: test_partial_reply_can_be_completed_without_changing_original_receipt and test_attendance_answer_supplies_actual_rows in tests/test_v3_edge_cases.py. Reproduced: 2 failed, 5 passed before the fix.
- Root cause: questions.answer marked every response answered, so later missing facts were discarded. Sources.apply_received_facts overlaid hospitality/vendor data but had no attendance branch.
- Production fix: retain immutable initial and follow-up replies; leave incomplete hospitality requests pending until recomputed documentation is complete; apply attributed attendance responses as visible rows without modifying original attendance or invoices.
- Kind of problem handled: multi-message evidence gathering and attendance recovery across every event. The failing assertions and public expected results were left unchanged.

## 2026-10-25 ? independent tool calls were still serialized

- Failing test: test_independent_reads_can_execute_concurrently_without_concurrent_audit_writes initially raised AttributeError because Toolbox had no call_many capability.
- Root cause: the migrated model loop processed every tool-use block sequentially, including independent reads required by spec ?9.
- Production fix: batch independent reads with bounded worker concurrency; append audit records in deterministic call order after reads complete. Mutations, primary case refreshes, and dependent actions remain sequential.
- Kind of problem handled: any model turn containing independent read/compute tools. The original concurrency assertion is unchanged.

## 2026-10-25 ? parallel reads shared a mutable SQLite cursor environment

- Failing test: test_independent_reads_can_execute_concurrently_without_concurrent_audit_writes failed intermittently in the full run with IndexError while converting an employees sqlite3.Row to a dict. The test passed alone, revealing a concurrency defect rather than a wrong expected outcome.
- Root cause: check_same_thread=False permits cross-thread access but does not make sharing a Python SQLite connection/cursor state safe. Each read worker rebuilt Sources on the same connection.
- Production fix: build one visible-source snapshot before parallel dispatch; read tools consume that snapshot without touching SQLite. Writes and audit appends remain ordered on the owning thread. The concurrency barrier and assertions are unchanged.
- Related investigation: two live P11 runs received API BadRequestError; preserve the provider's sanitized error message in failure reports so the actual cause can be diagnosed, rather than hiding it behind an exception class.

## 2026-10-25 ? first gift-limit breaches and email triggers lacked complete handling

- Failing tests: test_first_gift_over_the_limit_requires_tax_review and test_email_trigger_resolves_its_referenced_invoice failed with a missing flag and an explicit-event-only ValueError, respectively.
- Root cause: gift_limit_exceeded was emitted only from historical reclassifications, so a first gift over the limit could be routed as clean. Email/attendance dispatch only accepted pre-resolved event IDs.
- Production fix: emit the gift-limit detector from the recipient's actual annual computation, including first gifts. Resolve attributed email/attendance references from visible records, with a unique-current-period invoice fallback; ambiguous references require clarification. Close/year-end dispatch includes unattached invoices.
- Kind of problem handled: first-time and cumulative gift breaches; all supported trigger types. The failing expectations remain unchanged.

## 2026-10-25 ? confirmed anonymous recipients were treated as a tool failure

- Failing test: test_confirmed_unknown_gift_recipients_escalate_without_guessing returned hold instead of the required escalation.
- Root cause: the structured reply contract accepted only a complete named list, so it could not distinguish missing evidence from an organizer's explicit confirmation that some recipients are unknown.
- Production fix: accept a confirmed unknown_count, validate named plus unknown quantities against the invoice, retain anonymous distributions explicitly, and escalate without drafting a guessed tax treatment.
- The test and its expected outcome are unchanged; no gift threshold or policy changed.

## 2026-10-25 ? recorded delegations were not applied to actual approvals

- Failing test: test_recorded_absence_routes_tax_review_to_the_delegate showed every request still assigned to tax_manager during a recorded absence.
- Root cause: the pure delegation helper existed, but approval and escalation dispatch did not call it.
- Production fix: resolve a valid recorded delegate against the deadline, retain the required tax-manager approval role in the request's needs, and assign the actual delegate's role with an explicit delegation record. A second controller approval remains separate. Attributed rulings also accept a currently valid tax-manager delegate.
- The failing test and POL-MAT thresholds are unchanged.

## 2026-10-25 ? resumed cases lost review flags and retained unmanaged holds

- Failing test: test_recovered_case_keeps_its_review_history_and_hold_until_approval found that a later run dropped bewirtung_docs_missing after a human supplied the missing evidence.
- Root cause: flags and requests were preserved only in one Toolbox object's memory, not across runs; hold release was not connected to final human review.
- Production fix: restore prior decision flags/requests before recomputation, enforce an active case hold at posting, and release the case's holds only when an authorized person records final approval. The agent never releases its own hold.
- The unchanged test verifies later-run recovery, tax-manager routing, retained evidence, and human-controlled release.

## 2026-10-25 ? correction money failures did not reduce the amount-accuracy metric

- Failing test: test_wrong_correction_amount_is_not_counted_as_amount_accurate showed overall failure but amounts_correct=True for a wrong VAT-reversal amount.
- Root cause: the metric checked only top-level money fields and omitted the nested correction/entry amounts already checked by case comparison.
- Production fix: include corrections and entries in financial accuracy and publish measured catch-rate, false-positive-rate, and amount-accuracy aggregates with explicit numerators and denominators.
- The failing assertion, EUR 0.01 comparison tolerance, and public expected amounts are unchanged.

## 2026-10-25 ? planned approval roles could substitute for actual approval requests

- Failing test: test_submission_requires_actual_approval_requests_not_only_planned_roles demonstrated a successful submission after drafting without creating any approval request.
- Root cause: finalization checked that drafts existed, but treated computed routing as if its required actions had actually occurred.
- Production fix: require an actual valid request for every entry, the exact computed payroll rows, and the required recipient notices before submission. Drafting payroll also records the real payroll-export request. Compact export/notice results retain counts and sources; detailed rows remain in the database/API.
- The regression assertion and all expected finance outcomes are unchanged.

## 2026-10-25 ? browser preview exposed replacement characters

- Failing check: the real headless-browser Books preview displayed question marks where currency/separators belonged.
- Root cause: non-ASCII literals were degraded while passing source through the Windows shell pipeline.
- Production fix: use explicit JavaScript Unicode escapes for EUR and separators and HTML entities for static text. Financial values and calculations are unchanged.
- Validation: JavaScript syntax/currency checks and a second browser preview.

## 2026-10-25 ? protect proper German Unicode in generic-purpose detection

- Review finding: the Windows-pipeline replacement-character issue also affected an added generic-purpose literal.
- Fix: add the proper German character through an ASCII-safe Unicode escape, retaining recognition of degraded legacy text. A dedicated regression asserts that a documented but generic meal still requires a specific business purpose.
- No threshold, expected tax amount, or private holdout content was used or changed.
