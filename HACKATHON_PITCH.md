# Vouch: hackathon pitch

**Maximum: 3 minutes.** 351 spoken words; approximately 162 seconds at 130 words per minute. Read the pitch below; the notes afterward are not spoken.

## Spoken pitch

Every company event creates four accounting questions: what expenses are deductible, whether VAT can be recovered, what becomes taxable employee pay, and whether benefits to guests create additional tax.

Finance teams must reconcile invoices, actual attendance and each person's annual history. A late invoice can change an answer after the original month is closed.

Vouch fills the event-tax accountant seat at Nordlicht, a simulated German manufacturer with 450 employees.

A late fireworks bill moves a party from 107 euros and 43 cents to 111 euros and 40 cents per participant. That crosses the 110-euro VAT threshold in our rules. Vouch catches it and drafts a reversal of 6,175 euros previously claimed.

The depth is in what changes next. Companion costs belong to individual employees. An employee's annual allowances may need reassignment across earlier events. A December gift can change a March deduction. New facts must propagate through person-level ledgers, payroll drafts, VAT corrections and balanced accounting entries, while closed periods remain untouched.

We made three central design decisions.

First, one accountable agent owns each case. Before it acts, deterministic code builds a dated company snapshot, checks the evidence and computes treatment options. Jev selects structured decisions; typed tools reject invalid actions. Every amount uses exact decimal arithmetic and versioned rules, with company policies labeled separately.

Second, human collaboration is part of the workflow. Missing attendance triggers a request. A managers-only dinner goes to the tax manager. Their authorized answer is recorded, and the same case resumes using it.

Third, authority is enforced in code. Vouch drafts; independent reviewers approve; an authorized controller posts. The agent cannot approve itself, run payroll or file returns. A hash-chained audit record links actions to sources, rules and people. Provider failures pause safely.

In recorded live Jev validation, all fifteen public scenarios passed three repetitions with exact amounts and zero safety breaks. All twelve dedicated demo runs passed too. The company and human roles are simulated; model calls, calculations, approval gates and audit verification run for real.

Vouch handles the accountant's full follow-through: gather evidence, apply rules, maintain history, correct consequences and obtain the right review.

## Delivery cues

- Start on Dashboard while defining the four accounting questions and the accountant seat.
- Show the P11 Before / After card and the 6,175.00 VAT reversal during the fireworks example.
- Show one employee ledger or the correction list while explaining consequences across time.
- Point to the Case File, Approvals and Audit Log for the three design decisions; keep navigation brief.
- Show the saved validation summary for the measured results. Finish on the event comparison.
- Rehearse with a timer. Keep pauses short enough to finish by 3:00.

## Alignment with the attached Hyphenate brief

Based on all four pages of CalHacks_Hyphenate_Track (1).pdf:

| Judging emphasis | Evidence in the pitch |
|---|---|
| Realistic, messy finance work | Invoices, actual attendance, annual person history and late costs |
| A seat the team would hire | Event-tax accountant with explicit responsibilities and bounded authority |
| Workflow depth | Coupled expense/VAT/payroll/guest-tax treatments; retroactive re-evaluation; locked periods; balanced entries |
| Working with people | Requests, authorized tax-manager rulings and same-case resumption |
| Memorable catch | P11 threshold crossing and the prior VAT reversal |
| Accountable decisions | Typed actions, code-enforced approvals, source/rule citations and hash-chained audit |
| Proof and honest scope | Named live Jev validation and repetition counts; explicit simulated company/human roles |

The supplemental cases were agent-authored, so this pitch does not call them an independent blind benchmark. The owner holdout remains unscored. The pitch does not claim production authentication or real payroll/tax-authority integration. The single-agent design is intentional; it does not imply multiple collaborating AI agents.
