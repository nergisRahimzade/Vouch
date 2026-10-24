# Fix log

Each fix records the failing test, the root cause, what changed, and the kind of problem it now handles.

## 2026-10-23 — register could not prove its own year-to-date totals

- **Failing test:** `tests/test_w2_amounts.py::test_every_register_row_equals_the_engine` (employer FUTA, first file month, US).
- **Root cause:** the payroll register showed only year-to-date totals after each period. For the first month in the files, whether a capped base (FUTA, Social Security, German ceilings) was already exhausted could not be told from the file, so no checker (the test, and later Layer 4) could recompute the row. The test's back-solving guess was also wrong, and was removed rather than tuned.
- **What changed:** every register row now carries this period's taxable base for each capped contribution (`period_ss_wages`, `period_futa_wages`, `period_rv_base`, `period_kv_base`, `period_sv_days`, …), as real US and German registers do. Year-to-date before = year-to-date after − period base, exactly.
- **Kind of problem handled:** any check that recomputes pay from a register alone, for every capped base, in any month, including the first one loaded.
