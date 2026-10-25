# Vouch

Vouch is the event vendor payables specialist on Anthropic's finance team. It's a corner of Accounts Payable: it checks every event vendor bill against the contract and what was delivered, handles the deposits and accruals around each event, and never moves money without approval.

All Anthropic financial data, people, vendors and events are simulated. Entities: ENT-US (USD) and
ENT-UK (GBP). Built for the CalHacks Hyphenate track.

The full specification is `VOUCH_EVENT_AP_SPEC.md`. Follow its sections and task IDs (T1–T12,
tracked in `PLAN.md`; the module checklist is `MAPPING.md`). If this file and the spec disagree,
stop and ask.

## Ground rules

1. Works end to end before it gets detailed. Get every public case running through the real
   trigger flow before adding depth.
2. No AI math. Every amount, date and rate comes from deterministic code using `Decimal`, rounded
   half-up to cents. The model reads, decides between options code has computed, explains and asks.
3. Limits live in code, not in the prompt. The agent can never approve, post without approval,
   release a payment, edit vendor master data, or write to a closed period.
4. Every decision is traceable to the data, rule, clause, document or person behind it.
5. Honest results. Never show a number a run didn't produce. Label every score with the model,
   date and run count.
6. One command rebuilds everything from the fixed fixtures.
7. Ask me before changing scope or an accounting policy.

## Domain reference

### Glossary (plain words → finance words)

- **Bill** → invoice. **Deal** → contract; each rule inside it is a **clause** with an ID (e.g. `CAT-GUARANTEE`).
- **Order** → purchase order (PO). **Proof it happened** → evidence: badge scans, signed meal counts,
  crew logs, delivery receipts, rooming lists, load-out logs.
- **Money paid before the event** → deposit, held as a **prepaid** (asset) until the event date, then
  **released** to expense.
- **Cost we owe but haven't been billed for at month-end** → **accrual**, reversed on day 1 of the next month.
- **Fixing a wrong entry in a later month** → **reclass**. Closed months are never edited.
- **Asking the vendor to fix a bill** → **dispute**. **Waiting for missing info before paying part of a
  bill** → **hold**.
- **Who can say yes** → approval matrix. **Nobody approves their own request** → segregation of duties (SoD).
- **Paying early for a discount** → early-pay discount (e.g. 2/10 net 30 = 2% off if paid within 10
  days, else full amount due in 30).

### Chart of accounts

| Account | Name |
|---|---|
| 1000 | Cash |
| 1310 | Prepaid event costs |
| 1400 | VAT receivable (ENT-UK) |
| 2000 | Accounts payable |
| 2100 | Accrued liabilities |
| 6100 | Event venue and space |
| 6110 | Event catering |
| 6120 | Event AV and production |
| 6130 | Event merchandise |
| 6140 | Event lodging |
| 6190 | Purchase discounts (contra-expense) |

Every expense line carries entity, cost center and event tags. US sales tax is part of expense.
UK VAT on ENT-UK purchases goes to 1400.

### Approval matrix (POL-AP-01), summary

Amount = what Vouch would pay now or later (approved + held, not disputed), in USD at the invoice
month's period rate.

| Condition | Approvers |
|---|---|
| < $5,000, no flags/disputes/holds, not on enhanced review, not out of period | Standing policy (`policy`) |
| $5,000 – $24,999.99 | Event owner |
| $25,000 – $99,999.99 | Event owner + controller |
| ≥ $100,000 | Event owner + controller + VP Finance |
| Any dispute or hold | At least the event owner |
| Dispute ≥ $2,500 | Add controller |
| PO requester is the approving event owner (SoD) | Add controller |
| Out-of-period invoice | Controller |
| Close entries ≥ $10,000 (POL-CL-04) | Controller |
| Vendor on enhanced review (POL-AP-07) | No policy approval; at least event owner |

Same vendor + same PO within 7 days are summed for thresholds (POL-AP-05). An approver out of office
past the due date is replaced by their delegate. Nobody approves their own request.

### Which source wins

| Question | Source of truth |
|---|---|
| Price, minimums, fees | Contract clause |
| Meals served | Meal count signed by event ops > badge scans (+ staff roster only if the contract counts staff) > vendor's count |
| Hours worked | Crew log / load-out log |
| Goods received | Delivery receipt signed by event ops |
| Room-nights | Hotel pickup report confirmed by event ops |
| Bank account | Active account in vendor master (never an email) |
| Approval authority | Approval matrix + delegations |

## Decisions

The v1 repo never numbered its decisions D1–D17; the ground rules above and the testing rules below
are those standing decisions and stay in force. Rules that named payroll things (employees, grants,
castings, the generated world) are superseded by D18 and read in v2 terms: invoices, vendors,
contracts, events, payments and planted cases.

- **D18:** Domain changed to event vendor payables for Anthropic (simulated data); payroll retired to
  tag `payroll-legacy`.
- **D19:** Status is derived from visible rows; every scenario has `as_of` and the loader hides later rows.
- **D20:** Vouch cannot edit vendor master data; no tool exists for it. Bank changes require call-back + SoD.
- **D21:** A deterministic pre-pass runs before the agent; the agent receives findings and decides only
  judgment steps.
- **D22:** Scripted people in tests; model-played people only with `--live-people`.
- **D23:** Holdout cases live outside the repo, loaded only by the scorer from `VOUCH_HOLDOUT_PATH`,
  aggregates only. Never open, create or tune to them.
- **D24:** Performance changes are adopted only with zero accuracy regressions, measured by `bench`.

## Testing integrity

- A failing test means the logic is wrong somewhere. Find the root cause by reading
  the full error trace, reproducing the failure, and adding temporary logging or
  assertions. Never use try/except to hide or skip an error.
- Fix the root cause where it lives (a calculation, a check, a tool, the system
  prompt, a control, or the scenario loader) and fix the whole kind of problem, not the
  one case.
- Never change a test, a planted case's definition, the answer key (`expect` blocks), a
  threshold or the fixtures to make a result pass. Never loosen a planted case's conditions.
- If you believe a test is wrong, explain why and wait for my approval before
  touching it.
- No special cases: no code, prompt or tool may name a specific invoice, vendor, contract,
  event, payment, month or planted case.
- `expect` blocks are evaluator-only: they are stripped before the agent runs, and the agent must
  never be able to read them.
- Answer-key amounts must be checked independently (by hand), not only by Vouch's own engine.
- Holdout set (D23): I write the holdout cases and their answers. Never create, guess or read them.
- Record every fix in FIX_LOG.md: failing test, root cause, what changed, and which
  kind of problem it now handles.
- A skipped or disabled test counts as failing. Report it.

## Always ask me before

- changing any test, answer-key entry, planted case, threshold or fixture
- changing an accounting policy, tax assumption or scope
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
