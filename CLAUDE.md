# Vouch

Vouch is the event tax accountant on Nordlicht's finance team: it decides the German tax treatment of every company event, from deductibility and VAT to employees' taxable benefits, keeps every person's yearly totals, and never files or pays anything without approval.

Nordlicht Maschinenbau GmbH (Munich, ~450 employees, entity ENT-DE, EUR, calendar fiscal year) is
simulated: the company, people, events, invoices and emails are fictional. The German tax rules are
real; where the law leaves a gray area a company tax policy (`POL-…`, owned by the tax manager) decides,
and code labels such outcomes as policy, not law. Built for the CalHacks Hyphenate track.

The full specification is `VOUCH_DE_EVENT_TAX_SPEC.md`. Follow its sections and task IDs (T1–T12,
tracked in `PLAN.md`; the module checklist is `MAPPING.md`). If this file and the spec disagree,
stop and ask.

## Ground rules

1. Works end to end before it gets detailed. Get every public case running through the real
   trigger flow before adding depth.
2. No AI math. Every amount, date and rate comes from deterministic code using `Decimal`, rounded
   half-up to cents at the points the spec defines (POL-ROUND). The model classifies, gathers facts,
   asks people, decides between options code has computed, and explains.
3. Limits live in code, not in the prompt. The agent can never approve, post without approval, file a
   return, run payroll, change the yearly §37b election, or write to a closed period.
4. Every decision is traceable to the rule (ID and version), invoice line, attendance row, document or
   person behind it.
5. Honest results. Never show a number a run didn't produce. Label every score with the model,
   date and run count.
6. One command rebuilds everything from the fixed fixtures and seeds.
7. Ask me before changing scope, a tax rule, or a company tax policy.

## Domain reference

### Glossary (plain words → German / tax words)

- **Staff party** → *Betriebsveranstaltung*: social event for the company or an org unit, open to all its
  staff, more than half the attendees employees (plus companions).
- **Tax-free allowance** → *Freibetrag*: €110 per employee per event (incl. VAT), for at most 2 events per
  employee per year; only the excess is taxable pay.
- **Companion** → *Begleitperson*: their share of cost is attributed to the employee they came with.
- **Flat payroll tax** → §40(2) EStG: 25% on taxable event benefits, plus 5.5% solidarity surcharge and
  church tax, paid by the employer.
- **Business hospitality** → *Bewirtung*: 70% deductible, documentation required; input VAT recoverable if
  documented.
- **Gift** → *Geschenk*: deductible only while a recipient's gifts total ≤ €50 a year (net if VAT
  recoverable); above that, all of that recipient's gifts that year are non-deductible and VAT is blocked.
- **Promo item** → *Streuwerbeartikel*: ≤ €10 net per item; no recipient record, no flat tax.
- **Guest flat tax** → §37b EStG: 30% (plus surcharge and church tax) on the gross value of benefits to
  non-employees, elected once a year for all; not available above €10,000 per recipient per year.
- **Working meal** → *Arbeitsessen*: meal for employees on an exceptional assignment, ≤ €60 gross per person;
  not pay.
- **Input VAT** → *Vorsteuer*. **Closed month** → after the monthly VAT return deadline (the 10th of M+1).

### Chart of accounts (simulated, SKR-style)

| Account | Name |
|---|---|
| 1200 | Bank |
| 1400 | Input VAT receivable (Vorsteuer) |
| 1600 | Accounts payable |
| 1750 | Payroll flat tax payable (§40) |
| 1760 | Guest flat tax payable (§37b) |
| 6400 | Hospitality — deductible 70% |
| 6410 | Hospitality — non-deductible 30% |
| 6420 | Gifts — deductible |
| 6430 | Gifts — non-deductible (incl. blocked VAT) |
| 6440 | Staff events |
| 6445 | Customer events / program (non-gift benefits) |
| 6450 | §37b flat tax — deductible |
| 6460 | §37b flat tax — non-deductible |
| 6470 | §40 flat tax on staff events |
| 6480 | Promo items (Streuwerbeartikel) |
| 6490 | Working meals |
| 6500 | Wages — non-cash benefits (individually taxed) |

Blocked input VAT is added to the expense line it belongs to (or 6430 for gifts). Every expense line
carries event and cost center tags.

### Rules (spec §4.4, seeded in the `rules` table with sources)

| Rule | In short |
|---|---|
| R-BV-ALLOW / R-BV-MAX | €110 gross per employee per event, at most 2 qualifying events a year; excess taxable |
| R-BV-DEF | Social, open to all of the company or an org unit, > 50% employees + companions |
| R-BV-COST | All costs incl. VAT ÷ actual attendees incl. companions; companion share to the host |
| R-BV-FLAT | Taxable excess may be flat-taxed at 25% (+ surcharge + church tax) |
| R-BV-VAT | Input VAT only if gross cost per participant ≤ €110.00, else none for the whole event |
| R-BEW-70 / R-BEW-DOC / R-BEW-VAT | Hospitality 70/30; full documentation; VAT recoverable if documented |
| R-GIFT-50 / R-GIFT-REC / R-GIFT-VER | €50 per recipient per year (€35 before 2024); recorded with recipient name |
| R-PROMO | ≤ €10.00 net per unit: no recipient record, no §37b |
| R-37B-RATE / -CAP / -UNI / -DED / -NOTE | 30% of gross; €10,000 cap; uniform election; follows deductibility; notify |
| R-AE-60 | Working meal ≤ €60.00 gross per person: not pay |
| R-VAT-FIX | A reclaim that becomes blocked is reversed in the current open period |
| TAX-SOLI / TAX-KIST / TAX-VAT | 0.055; 0.07 (simulated); VAT read from the invoice, arithmetic validated |

Company policies (owned by P-TAX): POL-BV-OPT (allowance to the 2 events with the most exempt amount,
ties to the earlier), POL-BV-VATBASIS (VAT test per participant incl. companions), POL-BV-FLAT (always
flat-tax qualifying events; escalate non-qualifying), POL-BEW-02 (customer-event program is a 100%
deductible non-gift benefit, §37b on the partners' share), POL-LUMP (package lines itemized by the vendor,
never guessed), POL-37B-01 (flat tax outside the €50 test), POL-ROUND (rounding points), POL-MAT (routing).

### Routing (POL-MAT)

| Condition | Approver |
|---|---|
| No non-info flags, no taxable pay, no corrections, non-deductible < €1,000, §37b < €1,000 | `policy` |
| Any taxable pay, non-info flag, correction, non-deductible ≥ €1,000 or §37b ≥ €1,000 | `tax_manager` |
| Correction entries (reclass or VAT reversal) ≥ €5,000 | add `controller` |
| Gray area or a policy says escalate | `escalations: [tax_manager]`; nothing booked for that part |
| Tax manager out of office past a deadline | delegate (P-CFO) |

Payroll never approves; it receives `requests: [{to: P-PAY, about: payroll_export}]`.

## Decisions

The repo never numbered its v1 decisions D1–D17; the ground rules above and the testing rules below
are those standing decisions and stay in force. Rules that named an old domain's things are superseded
by D18 and read in v3 terms. The event-AP decisions D18–D24 are renumbered here; the domain-neutral ones
are kept in substance (time travel, pre-pass, scripted people, holdout, measured speed changes).

- **D18:** Domain is German event tax for Nordlicht Maschinenbau GmbH (simulated data, real rules); old
  domains retired to legacy tags (`payroll-legacy`, `event-ap-legacy`).
- **D19:** Status and per-person ledgers are derived from visible rows; every scenario has `as_of`.
- **D20:** Gray areas are explicit `POL-*` company policies; policy-based outcomes are labeled as policy.
- **D21:** Deterministic pre-pass before the agent; the agent decides judgment steps only.
- **D22:** Scripted people in tests; model-played people only with `--live-people`.
- **D23:** Holdout outside the repo, scorer-only, aggregates only; never open, create or tune to it.
- **D24:** Performance changes only with zero accuracy regressions, measured by `bench`.
- **D25:** Vouch cannot change elections, file returns, run payroll, or write closed periods; no tool
  exists for these.
- **D26:** Vouch remains single-agent: one accountant loop, deterministic tools, and human approvals. Claude and GPT are alternative backends, not collaborating agents.

## Testing integrity

- A failing test means the logic is wrong somewhere. Find the root cause by reading
  the full error trace, reproducing the failure, and adding temporary logging or
  assertions. Never use try/except to hide or skip an error.
- Fix the root cause where it lives (a calculation, a check, a tool, the system
  prompt, a control, or the scenario loader) and fix the whole kind of problem, not the
  one case.
- Never change a test, a planted case's definition, the answer key (`expect` blocks), a
  threshold, a rule, or the fixtures to make a result pass. Never loosen a planted case's conditions.
- If you believe a test is wrong, explain why and wait for my approval before
  touching it.
- No special cases: no code, prompt or tool may name a specific event, invoice, employee, business
  partner, vendor, month or planted case.
- `expect` blocks are evaluator-only: they are stripped before the agent runs, and the agent must
  never be able to read them.
- Answer-key amounts must be checked independently (by hand), not only by Vouch's own engine.
- Holdout set (D23): I write the holdout cases and their answers. Never create, guess or read them.
- Record every fix in FIX_LOG.md: failing test, root cause, what changed, and which
  kind of problem it now handles.
- A skipped or disabled test counts as failing. Report it.

## Always ask me before

- changing any test, answer-key entry, planted case, threshold, rule or fixture
- changing a tax rule, a company tax policy, or scope
- deleting data or rewriting git history
- anything a task in the spec doesn't cover

## How to report to me

I'm not familiar with finance jargon. After every task, report in this order:

### 1. In simple words
Explain like you're teaching a beginner, with no finance or software jargon. Use short
sentences and an everyday example if it helps.
- **What I did**
- **Why** (what problem it solves)
- **How** (the idea behind the solution)
- **Tests:** how many pass, how many fail or are skipped, and what any failure means
- **Needs your approval:** anything waiting on me, or "nothing"

### 2. In software and finance terms
The same report in proper terms, so I learn the vocabulary: files changed and what
each does, design decisions, accounting concepts involved (for example accrual,
forfeiture, reconciliation), and the exact test results.

Keep each report short: at most 5 bullets per section. If something needs my
approval, put it first and make it clear.
