# Vouch

Vouch fills the payroll accountant and equity compensation accountant seats at Northlight Labs, closing payroll and stock grants into the right entries every month, by department and entity.

## Commands

```bash
pip install -e ".[dev]"
python -m vouch rebuild              # one command: build the world from the seed and load it into ./vouch.db
python -m vouch.world build          # generate the company world from the fixed seed into ./world
python -m vouch.world build --clean  # the clean set: same company and noise, no planted mistakes
python -m pytest                     # run the tests
python -m vouch.reference.fetch_fx   # daily refresh of recorded exchange rates (needs OPENEXCHANGERATES_API_KEY in .env)
```

## Planted mistakes and the answer key

The generator plants 14 mistakes (some in the open month, some in earlier closed months) and 4 kinds of noise, which should be explained rather than flagged. The answer key goes to `./sealed/answer_key.json`, outside the world directory, so nothing that loads the world can read it. Only `vouch.evaluator` loads it. Each expected amount says how it was checked: `copied-from-source`, `independent-arithmetic` (plain arithmetic, not Vouch's engine) or `engine-output-pending-hand-check`. The build prints how many still need a hand check.

## Exchange rates

Rates are recorded daily USD rates from [Open Exchange Rates](https://openexchangerates.org), one per business day, snapshotted in `vouch/reference/fx_oxr_usd.csv`. Builds read only the snapshot, so they work offline and come out the same every time. Each month closes at that month's average rate. A month can't close until every business day in it has a rate (policy FIN-02).

To refresh daily on Windows, run `scripts/register_daily_fx.ps1` once. It registers a scheduled task.

## Tax rules and simplifications

Statutory rates live in `vouch/calc/tax_rates.json`. Each value has a source link, a tax year, and a `checked` label: `official-document`, `secondary-confirmed` or `recalled`. Every value was checked against its source on 2026-10-23. Most come from the official text (IRS, SSA, gov.uk, German statutes); the German social-insurance rates and ceilings were checked against two reputable secondary sources (krankenkassen.de, vdek). The 2026 FUTA rate and wage base are still `recalled`, unchanged by statute since 2011, until the 2026 Form 940 instructions are read. Only the main components are covered:

- **All entities:** monthly payroll paid on the last business day. Partial months are prorated by calendar days. No bonuses, benefits in kind, or pre-tax deductions.
- **US:** federal only, no state or local tax. Federal income tax uses the Pub 15-T percentage method for automated payroll, with a 2020+ Form W-4, Single, Step 2 unchecked and no other adjustments. Social Security uses its wage base, plus Medicare, Additional Medicare above $200,000 year to date, the employer match, and FUTA at 0.6% on the first $7,000 (full state credit assumed). Monthly payroll, not semi-monthly.
- **UK:** tax code 1257L on a month-1 (non-cumulative) basis, England/NI bands. The personal-allowance taper above £100,000 is not applied. Tax uses exact monthly bands (annual ÷ 12) rounded down to the penny, not HMRC's printed tables. NI is category A, worked out per period on monthly thresholds. The employer pension is the company's 5% of gross (pay policy PAY-04), with no employee pension contribution and no salary sacrifice.
- **Germany:** Steuerklasse I, no church tax, no child allowances. Wage tax applies the § 32a EStG tariff to annualised pay. That pay is reduced by the employee lump sum (€1,230), the special-expenses lump sum (€36) and the employee's actual pension, health and care contributions, a stand-in for the official Vorsorgepauschale. Partial months use social-insurance days (30 per month, 360 per year). Contribution ceilings apply as pro-rata annual ceilings, tracked year to date. Health insurance uses the average Zusatzbeitrag, and care insurance has no childless surcharge.
- **Checked payslips:** `tests/test_t2_payslips.py` holds two payslips per country, worked out by hand with every step shown: an ordinary month, plus the Social Security wage base and Additional Medicare (US), the additional rate (UK), and December contribution ceilings with the solidarity surcharge (DE). They still need confirming with the official calculators (IRS, HMRC, BMF).
- **Stock compensation:** grant-date fair value is expensed straight-line over 48 months by monthly service anniversaries, with a catch-up in the board-approval month. Forfeitures are recognised when they happen.
