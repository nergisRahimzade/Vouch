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
