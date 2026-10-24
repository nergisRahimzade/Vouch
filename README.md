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

## Reconciliation

`python -m vouch.recon run` compares every source with every other for the same fact, across all loaded months, and stores what it finds for the open month's close. Each finding names the sources it compared and what each said, the trusted source and why (HR for people, dates, entities and bank details; approved salary changes for salary; signed grants and board approvals for grants; the bank for cash; the pay policy for rates), the rules, the amount, a proposed action and who to ask. Earlier rulings about the same kind of problem are cited.

**Materiality (FIN-01):** a measurement difference strictly below the threshold for its currency (USD 50, GBP 40, EUR 45) is explained and logged; at or above it, it is a case. This applies only where two sources measure the same amount differently (rates, rounding, a payment against its net pay). Control exceptions, such as an unknown person, a duplicate, changed bank details, a missing approval or a self-approval, are always cases, whatever the amount.

## The agent

```bash
python -m vouch close                 # the close, run by Claude (claude-opus-5-5) through typed tools
python -m vouch close --scripted      # a fixed playbook for fast tests only; every run is labelled "scripted"
python -m vouch colleagues            # demo: Claude plays the approvers (scripted fallback: --scripted)
python -m vouch audit                 # demo: an auditor asks Vouch to defend cases
python -m vouch why <case-id>         # Vouch defends a decision from the audit trail only
python -m vouch.controls verify       # check the audit trail's hash chain
```

Code runs a fixed checklist: load, run payroll per entity, run stock expense, reconcile, work each case, draft entries, request approvals, report. The model decides what to do with each case (hold, ask whom, draft, request) and explains it. Code does all the math: the model never types an amount or a name. Every tool call goes to the audit trail. Every number, date, rule and name in a report, a case decision or a defence must match a tool result; otherwise it is rejected. A tool error, a missing rate, a model timeout or refusal, or a colleague's half-finished work ends in a held case with a plain reason. Instructions found in emails are treated as data and reported as suspicious. Each run records the model, the date, its token use and its cost.

## Proof it works

```bash
python -m vouch score                  # the real model (claude-opus-5-5) on a fresh copy of the company, scored against the hidden key
python -m vouch score --scripted       # the fixed playbook, for fast tests only; every report says "SCRIPTED"
python -m vouch score --runs 3 --seed N
```

The evaluator (`vouch/evaluator`) generates the company from the seed with the generator's own command, loads it into a new database, lets the agent run the close, and lets scripted colleagues answer, approve, post and release, so the score measures the agent, not the colleagues. It then compares what is in the database with the sealed answer key. The agent never sees the key. Each report is saved in `results/` and names the set, the mode, the model, the date, the number of runs, the seed and the cost.

| Measure | How it is measured | Target |
| --- | --- | --- |
| Caught | Each planted mistake handled as its expected outcome requires (a hold on the right payment or person, a draft in the open month, a funding request for exactly the unpaid lines, a request routed to the controller, ...), on a case the agent did not fail on | 100% |
| False holds | Clean people and payments of the open month that Vouch held or questioned, as a share of all clean ones (worst run) | under 5% |
| Evidence complete | The cases cite every rule, precedent, source, trusted source and person the key requires | 100% |
| Money correct | Every entry balances; the amount each case acted on, and every entry drafted for it, matches the key | 100% |
| Never | Forbidden actions that went through: a self-approval, a double payment, a control action by the agent. Refused attempts are not counted | 0 |

## Controls

Limits live in code (`vouch/controls`), not in a prompt. Every write checks the actor's role, and a refusal is logged before it is raised.

| Role | Can do |
| --- | --- |
| Vouch (agent) | Read everything, draft entries, open cases, place holds, ask questions, request approval or funding |
| Controller | Approve and post entries, reopen a closed month, final sign-off |
| Finance manager | First approval on entries |
| HR | Confirm employment and bank details (with a call-back) |
| Treasury | Release payments |
| Stock admin | Confirm grants and board approvals |

Rules enforced: the requester can't approve; whoever keyed a change can't approve it; an entry at or above the large-entry limit needs two different approvers, one of them the controller; closed months are locked; a bank change needs HR plus a call-back; there is no expense for a grant without board approval; duplicates are refused; and a held payment can't be released. A draft becomes posted only through an approved request, and the database itself refuses any other path.

## Exchange rates

Rates are recorded daily USD rates from [Open Exchange Rates](https://openexchangerates.org), one per business day, snapshotted in `vouch/reference/fx_oxr_usd.csv`. Builds read only the snapshot, so they work offline and come out the same every time. **Policy (FIN-02, chosen by the human for simplicity):** each month is closed at that month's average rate, the mean of its business-day rates, for expenses and balances alike, so no separate month-end rate is used. A month can't close until every business day in it has a recorded rate, and an older rate is never reused. Every converted amount names its rate, the month and the business days it came from (`fx_rate_basis` in the files, `fx_basis` in the ledger). The ledger refuses a converted line that doesn't.

To refresh daily on Windows, run `scripts/register_daily_fx.ps1` once. It registers a scheduled task.

## Tax rules and simplifications

Statutory rates live in `vouch/calc/tax_rates.json`. Each value has a source link, a tax year, and a `checked` label: `official-document`, `secondary-confirmed` or `recalled`. Every value was checked against its source on 2026-10-23. Most come from the official text (IRS, SSA, gov.uk, German statutes); the German social-insurance rates and ceilings were checked against two reputable secondary sources (krankenkassen.de, vdek). The 2026 FUTA rate and wage base are still `recalled`, unchanged by statute since 2011, until the 2026 Form 940 instructions are read. Only the main components are covered:

- **All entities:** monthly payroll paid on the last business day. Partial months are prorated by calendar days. No bonuses, benefits in kind, or pre-tax deductions.
- **US:** federal only, no state or local tax. Federal income tax uses the Pub 15-T percentage method for automated payroll, with a 2020+ Form W-4, Single, Step 2 unchecked and no other adjustments. Social Security uses its wage base, plus Medicare, Additional Medicare above $200,000 year to date, the employer match, and FUTA at 0.6% on the first $7,000 (full state credit assumed). Monthly payroll, not semi-monthly.
- **UK:** tax code 1257L on a month-1 (non-cumulative) basis, England/NI bands. The personal-allowance taper above £100,000 is not applied. Tax uses exact monthly bands (annual ÷ 12) rounded down to the penny, not HMRC's printed tables. NI is category A, worked out per period on monthly thresholds. The employer pension is the company's 5% of gross (pay policy PAY-04), with no employee pension contribution and no salary sacrifice.
- **Germany:** Steuerklasse I, no church tax, no child allowances. Wage tax applies the § 32a EStG tariff to annualised pay. That pay is reduced by the employee lump sum (€1,230), the special-expenses lump sum (€36) and the employee's actual pension, health and care contributions, a stand-in for the official Vorsorgepauschale. Partial months use social-insurance days (30 per month, 360 per year). Contribution ceilings apply as pro-rata annual ceilings, tracked year to date. Health insurance uses the average Zusatzbeitrag, and care insurance has no childless surcharge.
- **Checked payslips:** `tests/test_t2_payslips.py` holds two payslips per country, worked out by hand with every step shown: an ordinary month, plus the Social Security wage base and Additional Medicare (US), the additional rate (UK), and December contribution ceilings with the solidarity surcharge (DE). They still need confirming with the official calculators (IRS, HMRC, BMF).
- **Rounding:** every amount is rounded half up to the cent. When one amount is split across departments or entities, each share is rounded and the leftover cent goes to one fixed line: the first, in the company's fixed order (Engineering, Product, GTM, G&A; US, UK, DE), that has a share. A translated amount is converted once, then split (`vouch/calc/rounding.py`).
- **Stock compensation:** grant-date fair value is expensed straight-line over 48 months by monthly service anniversaries, with a catch-up in the board-approval month. Forfeitures are recognised when they happen.
