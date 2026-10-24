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
