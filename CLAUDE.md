# Vouch

Vouch is an autonomous payroll and stock-compensation accountant for Northlight Labs,
a fictional ~300-person startup with entities in the US (USD), UK (GBP) and Germany (EUR).
Built for the CalHacks Hyphenate track.

Seats it fills: payroll accountant and equity compensation accountant. It closes payroll
and stock grants into the right entries every month, by department and entity.

The full specification is the "Vouch — Build Specification" document. Follow its layers
and task IDs. If this file and the spec disagree, stop and ask.

## Ground rules

1. Works end to end before it gets detailed. Get one month closing correctly across
   US, UK and DE before adding depth.
2. No AI math. Every amount, date and rate comes from deterministic code. The model
   reads, decides, explains and asks.
3. Limits live in code, not in the prompt. The agent can never approve, post, reopen
   a closed month or move money.
4. Every decision is traceable to the data, rule, document or person behind it.
5. Honest results. Never show a number a run didn't produce. Label every score with
   the model, date and run count.
6. One command rebuilds everything from a fixed seed.
7. Ask me before changing scope or an accounting policy.

## Testing integrity

- A failing test means the logic is wrong somewhere. Find the root cause by reading
  the full error trace, reproducing the failure, and adding temporary logging or
  assertions. Never use try/except to hide or skip an error.
- Fix the root cause where it lives (a calculation, a check, a tool, the system
  prompt, a control, or the generator) and fix the whole kind of problem, not the
  one case.
- Never change a test, a planted case's definition, the answer key, a threshold or a
  seed to make a result pass. Never loosen a planted case's conditions.
- If you believe a test is wrong, explain why and wait for my approval before
  touching it.
- No special cases: no code, prompt or tool may name a specific employee, grant,
  payment, month or planted case.
- Cast people (castings) must look exactly like normal hires. `castings()` and the
  answer key are generator/evaluator-only; the agent must never be able to read them.
- Answer-key amounts must be checked independently (by hand or with the official
  government calculator), not only by Vouch's own engine.
- Held-out set: write only the plant mechanics. I write the held-out case
  descriptions and their answer key. Never create, guess or read them.
- Record every fix in FIX_LOG.md: failing test, root cause, what changed, and which
  kind of problem it now handles.
- A skipped or disabled test counts as failing. Report it.

## Always ask me before

- changing any test, answer-key entry, planted case, threshold or seed
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