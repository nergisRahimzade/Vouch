# Vouch: three-minute demo script

**Hard limit: 3:00.** Read only the quoted narration. The screen directions are silent. Use a steady conversational pace; the narration is 320 words, leaving time for clicks and pauses. Rehearse once with a timer.

Open http://127.0.0.1:8080/. Refresh with Ctrl+Shift+R and run P11 live once before recording so its new Before / After card is ready.

## 0:00–0:25 — Open with the catch

**Show:** P11 Case File → Before / After card, then its VAT reversal under Corrections.

> “This late fireworks bill changes the tax treatment of an entire company party. The cost per participant rises from 107 euros and 43 cents to 111 euros and 40 cents. Vouch catches the change and drafts a reversal of 6,175 euros in VAT previously claimed.”

## 0:25–0:50 — Explain Vouch and the problem

**Show:** Dashboard, with the Live agent control visible.

> “Vouch is the event tax accountant on Nordlicht's finance team: it decides the German tax treatment of every company event, from deductibility and VAT to employees' taxable benefits, keeps every person's yearly totals, and never files or pays anything without approval. It connects invoices, attendance and earlier accounting records.”

## 0:50–1:10 — Run the agent live

**Do:** Select P11 → Live agent → Process event. Show its steps and resulting comparison.

> “I'm processing that event live. Vouch uses one agent, powered by Jev, to choose the treatment and workflow. Code calculates every amount and enforces the limits. Here are the updated costs, the correction and the supporting rules.”

## 1:10–1:40 — Show human control and the books

**Do:** Open Approvals. For one entry, show its tax-manager and controller requirements. Approve both steps with review reasons. Open Books, find that entry, expand it and choose Post as controller with a reason. Leave other entries pending if needed.

> “Vouch prepares the work, but it cannot approve or post its own entries. This entry requires review from the tax manager and controller. Each review has a recorded reason. Once those approvals are complete, the controller can post it. Payroll stays a draft, and closed accounting periods remain protected.”

## 1:40–2:10 — Show a judgment call and a human answer

**Do:** Dashboard → run P10 live. Show Escalated. Open Approvals → Questions for the finance team. Choose Individual payroll taxation, enter “Individual payroll treatment authorized,” and click Record tax-manager answer and continue. Show the resumed Case File.

> “For this managers-only dinner, Vouch needs the tax manager's judgment. It escalates instead of guessing. I'm recording the manager's decision to use individual payroll taxation. The same case now resumes, follows that authorized answer and prepares the treatment. The answer stays attached to the case as recorded memory.”

## 2:10–2:30 — Show accountability

**Do:** Open Audit Log. Expand a model call or human answer. Point to Recorded time, Scenario time and Chain verified.

> “The audit log connects each action to its evidence, rules and responsible person. Actual execution time is separate from the scenario's accounting date. The verified audit chain helps detect changes to past records.”

## 2:30–3:00 — Give proof and close

**Show:** The saved successful public and demo results, then finish on the P11 comparison. Use the actual validation report, not an invented score graphic.

> “Jev passed all fifteen public scenarios across three repetitions, with exact amounts and zero safety breaks in those tests. All twelve dedicated demo runs passed too. Nordlicht, colleague replies and reviewer identities are simulated. The model calls, calculations, approval checks and audit verification run for real. Vouch gathers the facts, catches changes and knows when a person must decide.”

---

## Why this version fits the attached hackathon brief

The PDF asks the video to open with a memorable catch and prove realistic execution, limited authority, collaboration and traceable decisions. P11 supplies the catch and change over time. Approvals and Books show authority limits. P10 shows asking a person and handling the answer. The audit and measured results provide evidence. P01, P03 and the custom-event form are reserved for questions or live judging so the recording stays within three minutes.

## Recording notes — do not read aloud

- Keep any waiting-time cuts visibly labeled; never substitute Scripted demo footage for a live run. If the run finishes early, use that time to point to evidence rather than adding narration.
- Approvals, entries and ledgers belong to the current scenario snapshot. Show P11's reviews and books before starting P10.
- Reviewer identities are simulated; there is no deployed production authentication or real email, payroll or tax-filing connection.
- Use `results/jev-integration-summary-2026-10-25.json` for the closing evidence. Four additional agent-authored cases also passed twelve live executions with their answers withheld from Vouch; they are not an independent owner holdout. Mention this only in questions, not as extra narration.
- Keep .env, API keys and private files off screen. The Books window covers ten calendar years; it does not claim ten years of populated transactions.
- Stop at 3:00. Do not add the longer walkthrough from earlier drafts.
