# Updates

A running record of every change to the program: when it was made, on which branch, what changed and why, and which part of the Hyphenate track brief it serves. A new entry is added with every change, newest at the bottom. Times are local (UTC−05:00). `FIX_LOG.md` keeps the root-cause detail of each fix; this file is the plain record.

Entries 1–7 were written after the fact. Their times come from the changed files' last-modified stamps, so they are accurate to the minute the files were last saved.

---

## 1. 2026-10-24, before 13:10 · branch `dashboard`

**Branch brought up to date.** `dashboard` was fast-forwarded onto `origin/layer7-e3-consistency`, the most recent branch (Layers 1–7). No new commit was made.

## 2. 2026-10-24, 13:10–13:22 · branch `dashboard`

**Added the dashboard (spec Layer 8, U1–U6).**
- **What:** a web app, started with `python -m vouch ui`, with seven pages: Overview, Audit trail, Human review, Cases, Ledger, Results, and Run & sandbox.
- **How it gets its data:** it reads only what a close left in the database and the evaluator's saved reports. Every human action (approve, reject, answer, post, release, payment run) goes through the existing controls as the named person in the chosen role.
- **Why (track):**
  - "Explainable and accountable": every call is traceable on screen.
  - "Limits": choose Vouch as the role and it is refused in code, and the refusal is logged.
  - "Working with others": the approvals inbox and Vouch's questions.
  - "A live demo, running for real… a scenario of our own": the judge sandbox.
  - "Proof it works": the labelled scores.
- **Files:** `vouch/ui/__init__.py`, `vouch/ui/server.py`, `vouch/ui/jobs.py`, `vouch/ui/static/index.html`, `app.css`, `app.js`, `vouch/__main__.py` (the `ui` command), `pyproject.toml` (optional `[ui]` extra: fastapi, uvicorn; `dev` adds httpx), `README.md` (Dashboard section).
- **Tests:** `tests/test_u_dashboard.py`.

## 3. 2026-10-24, 13:19 · branch `dashboard`

**The evaluator owns how each company set is built.**
- **What:** `vouch/evaluator/run.py` has a new `build_command()`. Both the evaluator and the dashboard's sandbox use it, and `prepare()` behaves as before.
- **Why:** the project rule that nothing outside the generator and evaluator may know how the held-out set is built. The rule exists so the agent can never see the held-out mistakes ("Proof it works": the held-out score stays honest). My first dashboard draft broke the rule, and the existing guard test caught it. The test was not changed.

## 4. 2026-10-24, 13:46 · branch `dashboard`

**The live agent could not work any case: fixed.**
- **What happened:** the first live close held all 14 cases with "the model API returned an error (400)". The agent sent 21 tools marked strict, and Claude's API accepts at most 20.
- **What changed:** in `vouch/agent/policy.py`, strict schemas now apply only to the tools whose input reaches a calculation or a control (compute and act tools: hold, ask, draft, request, record, compute, translate). Read tools take one ID and answer "not found" if it is wrong. The agent stops with a plain error if the limit is ever exceeded.
- **Why (track):** without this, the agent cannot run for real, and "a live demo, running for real" is a submission requirement. The limits themselves are untouched; they live in `vouch/controls`.
- **Tests:** `tests/test_g2_model_tools.py` (new). No existing test changed.

## 5. 2026-10-24, 14:06–14:09 · branch `dashboard`

**A question may only promise a next step that can be carried out.**
- **What happened:** in the live colleagues round, Claude asked a department head about an entity transfer and promised "if yes, I'll draft a correcting entry". No entry can settle that kind of case, so the yes answer crashed the whole round.
- **What changed:**
  - `vouch/agent/templates.py`: `cannot_correct()` says, from one table of correctable kinds of case, whether a correcting entry can be drafted.
  - `vouch/agent/tools.py`: the `ask` tool refuses such a promise and tells the model which steps it can promise instead. The refusal is logged like every tool call.
  - `vouch/agent/questions.py`: if such a promise was already made, the answer holds the case with a plain reason instead of crashing.
- **Why (track):**
  - "Asks at the right moment, and handles a no": a question must say truthfully what each answer leads to.
  - "Recovers from something going wrong": a broken promise ends in a held case with a reason, never a crash or a guess.
- **Tests:** `tests/test_g3_promises.py` (new). No existing test changed.

## 6. 2026-10-24, 14:09–14:14 · branch `dashboard`

**Reset works even while the database is open elsewhere.**
- **What happened:** Reset failed with `WinError 32 … being used by another process` because a second dashboard had `vouch.db` open, and Windows won't delete an open file.
- **What changed:** `vouch/ui/server.py` builds the new company in `vouch.db.rebuild` and copies it into `vouch.db` in place, so nothing is deleted. `.gitignore` ignores the temporary file.
- **Why (track):** "A live demo… we may ask you to try a scenario of our own". Reset has to work every time in front of judges.
- **Tests:** `test_u6_reset_works_while_another_program_holds_the_database_open`.

## 7. 2026-10-24, 14:14 · branch `dashboard`

**Colleagues round before any close: a plain message instead of a crash.**
- **What happened:** running the colleagues round on a freshly reset company failed with "no such table: findings".
- **What changed:** `vouch/ui/server.py` now answers "there is nothing for colleagues to answer yet: run the close first".
- **Tests:** a check added to `test_u6_sandbox_rebuilds_the_clean_company_and_its_close_opens_no_case`.

## 8. 2026-10-24, 14:24 · branch `dashboard`

**Started this file.** It also records the review of every change above against the track brief. Each change answers a real failure seen in a live run, or a requirement of the brief or the spec. None was made to get a test to pass. No existing test, answer-key entry, planted mistake, threshold or seed was changed.

## 9. 2026-10-24, 14:38 · branch `dashboard`

**When the close report can't be written, the run ends honestly instead of hanging.**
- **What happened:** in a live check, Claude's API became unreachable near the end. The affected case was held correctly. But the report call then failed too, and the close stopped dead: the run stayed "running" forever, the report step never finished, and the cost showed **$0.00** although about $3 had been spent.
- **What changed:**
  - `vouch/agent/runner.py`: if the report can't be written, the run is recorded as `failed` with the reason, the real tokens and cost are saved, and the failure goes in the audit trail. Nothing done so far is lost, and running the close again finishes the report. No report is ever invented.
  - `vouch/__main__.py` and `vouch/ui/static/app.js`: they say "report not written: …; run the close again to finish it" instead of "rejected" or an endless "running".
- **Why (track):**
  - "Recovers from something going wrong", the moment.
  - Spec ground rule 5: never show a number a run didn't produce. The $0.00 cost was such a number.
- **Tests:** `tests/test_g6_report_failure.py` (new). While writing it, my first version expected the wrong number of drafts. I corrected the test's expectation, because the program was right. No existing test changed.

**Also confirmed in the same live run:** the promise check from entry 5 works with the real model. Twice Claude tried to promise a correction that couldn't be made, was refused, and asked again with a step it could keep. The live colleagues round, which crashed before, now completes.

## 10. 2026-10-24, 14:54 · branch `dashboard`

**Denied answers to Vouch's questions are now logged, like every other refusal.**
- **What happened:** if someone in the wrong role answered one of Vouch's questions, or answered one that was already answered, the attempt was refused but left no row in the audit trail. Every other refusal in the program is logged.
- **What changed:** `vouch/agent/questions.py` refuses through the same gate as the rest of the controls (`gates.refuse`). The attempt is written to the audit trail, with the rule (`ROLE` or `FIN-08`) and the question, before it is refused.
- **Why (track):**
  - Workshop: "Denied attempts are logged too".
  - Brief: "every call should trace back to the data, rule or person behind it".
- **Not done, on purpose:** logging every change as a before → after diff (a workshop suggestion, not a judging criterion). The audit trail already records who, what, when, why, the evidence and the rule for every action. Diffs would add little for the judges.
- **Tests:** an extra check in `test_u3_an_answer_leads_to_the_step_the_question_promised` (written this session): both kinds of denied answer show in the audit trail and the chain still verifies. No pre-existing test changed.

## 11. 2026-10-24, 16:47 · branch `efficiency` (created from `dashboard` at `bf21181`)

**Housekeeping, no code change.**
- Pushed `dashboard` (`60c6225`, `bf21181`) and created `efficiency` from it for the cost and speed work.
- Opened PR #1, `dashboard` → `layer7-e3-consistency`: https://github.com/nergisRahimzade/Vouch/pull/1. It lists the dashboard and the six live-agent fixes.
- The efficiency PR will target `dashboard`, and GitHub moves it to `layer7-e3-consistency` once PR #1 is merged.
