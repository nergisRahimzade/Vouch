# Answering the judges: proof

## What to say (about 35 seconds)

“The company, its records and the human roles are simulated. The Jev calls, calculations, approval checks and audit verification execute for real. In our recorded validation, all fifteen public scenarios passed three repetitions with exact amounts and zero safety breaks in those tests. All twelve dedicated demo runs also passed. The expected answers stay in the scorer and are withheld from Vouch. You can enter your own staff-event numbers here and run the same live workflow. We also tested a provider failure during execution: the case paused safely without posting.”

## What to show

1. Open results/jev-integration-summary-2026-10-25.json: show public.cases=15, runs=3, passed_all_runs=15, amounts_correct_all_runs=15 and safety_violations=0. Then show dedicated_demo.passed_runs=12 and the resolved model ID.
2. Show a live Case File with its computed amounts and source/rule references. For the memorable catch, use P11's 107.43 to 111.40 comparison and 6,175.00 prior VAT reversal.
3. Show Approvals and Books. An unapproved entry must be refused by Post as controller; after the required reviews, human posting is permitted. Do not claim an approval or posting that you have not actually performed.
4. Show Audit Log: expand an actual model call, correction or human answer. Show execution time, separate scenario time and Chain verified.
5. Offer the judge Dashboard → Try your own scenario. Let them change the employee count and invoice values, then click Run my scenario live. The result is computed from their inputs; it is not a prerecorded result.

## What to emphasize

- Exactness: compare amounts to cents, rather than presenting a fluent explanation as proof.
- Consistency over time: one late invoice can trigger both historical VAT corrections and payroll changes, with closed periods protected.
- Authority boundaries: an invalid action is refused in code, even when the model or a user requests it.
- Honest evaluation: fifteen public scenarios repeated three times demonstrate performance on that set, not universal tax correctness. Four additional cases were agent-authored, with expectations withheld from Vouch; they are not an independent blind holdout. The owner holdout remains unscored.
- Real scope: this is a simulated company with live inference and real accounting/control execution. Reviewer identity/authentication, company systems, emails and payroll/tax filing are not production integrations.

## Ready-to-use judge example

Dashboard → Try your own scenario → Fill example. This fills two employees and a net/VAT/gross invoice of 100.00 / 19.00 / 119.00. Judges may edit every field, including the invoice's VAT rate. Run my scenario live should show 59.50 per participant for the unchanged example. Annual employee history may affect taxable benefits, so do not promise a zero-tax result merely from that per-participant amount.
