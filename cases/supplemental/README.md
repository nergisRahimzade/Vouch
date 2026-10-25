# Supplemental cases

These four cases were authored by Codex at the user's explicit request. They are hidden from the UI scenario list and loaded only when the scorer receives this directory. They are supplemental regressions, NOT an independent or blind holdout. The owner-provided VOUCH_HOLDOUT_PATH remains untouched.

Independent arithmetic, fixed before running the system:
- S01: 220.00 / 2 = 110.00. Each employee is exactly at the allowance. VAT 35.13 remains recoverable. Net 184.87 + VAT 35.13 = gross 220.00.
- S02: 220.02 / 2 = 110.01. Taxable excess is 0.01 per employee, total 0.02. Whole-event VAT 35.13 is blocked, so deductible expense is 220.02. Payroll flat tax: 0.02 x 25% = 0.005, half-up to 0.01; solidarity and church components round to 0.00.
- S03: 120.00 / 2 = 60.00, exactly the exceptional working-meal limit. Net 100.84 + VAT 19.16 = 120.00; no taxable wages.
- S04: documented hospitality net 400.00 x 70% = 280.00 deductible, 120.00 non-deductible; 76.00 input VAT recoverable.

Run: `python -m pytest tests/test_supplemental_cases.py -q`. For a billed live score use the existing evaluator with `directory=Path('cases/supplemental')`, three repetitions, and `model_options={'model':'jev-latest'}`. Expected answers are stripped before the agent receives the scenario. Do not tune model prompts to this set's results.
