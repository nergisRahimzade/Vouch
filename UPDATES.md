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

## 12. 2026-10-24, 17:05 · branch `efficiency` · Phase 0: spending guard

**What a live run will cost is now shown before it starts, shown while it runs, and capped.**
- **Estimate, from measured data only.** New `vouch/agent/cost.py`. The estimate for a live close is the last finished live close in the same database; if there is none, it is the figure measured on 2026-10-24 ($3.48), and the page says so. A live score is shown as runs × that.
- **Asked before spending.** The dashboard shows "Estimated $X (basis)… Spending cap $Y" with *Confirm and start* / *Cancel* before any live close, colleagues round or score. `python -m vouch close` and `python -m vouch score` print the same and ask `[y/N]`; a non-terminal must pass `--yes`.
- **Spending cap per run** (default $6.00, allowed $0.25–$50; `--budget` on the command line, a field on the page). `ModelPolicy` stops before a call if the run has already spent the cap. The cases not yet worked are held with a plain reason, and the run ends as `failed` with "report not written" and its real cost. Nothing is guessed. A score records its per-run cap on the report.
- **Cost shown while running.** The runner saves the run's tokens and cost after every case, so the Overview shows "$x of $cap" as it goes. Before, the cost was saved only at the end (the earlier hang left it at $0.00). Colleagues and "Ask why" now report their real cost too.
- **Defaults:** effort stays "medium"; the Score button defaults to the main set, with a note that a live score is at least 3 full closes.
- **Why (track):** "Would you hire it?": limited access and honest accounting extend to money. Spec ground rule 5: never show a number a run didn't produce; every figure names its source.
- **Tests:** `tests/test_x_cost_guard.py` (8, all with a stand-in client that bills known tokens, so none spends money): cap stops calls and holds the rest, cost saved mid-run, estimate from the last live run, bad caps refused, estimate endpoint, CLI will not start unasked (close and score).
- **Also fixed (bug in my dashboard, PR #1):** with several requests at once the page could get random 500 errors ("SQLite objects created in a thread can only be used in that same thread"), because the server opens and uses a request's connection on different threads. `vouch.db.connect` now lets a connection cross threads; code that shares one between threads takes turns. Found when a repeated render check failed 4 times in 6. Tests: the page asking for many things at once; a connection used from another thread. After the fix: 0 errors in 6 repeats.
- **Verified:** 58 tests in the affected areas pass; every dashboard view renders against a live server 6 times with no server errors. No live API call was made in this phase.

## 13. 2026-10-24, 17:11 · branch `efficiency` · Phase 1: cache the whole conversation

**Each turn now marks the end of the conversation so far as cacheable, so the API bills what it has already seen at the cached rate.**
- **The problem:** every turn re-sends the whole conversation for the case (the case, each tool result, each reply). Only the fixed instructions were cached, so the rest was billed at the full input rate on every turn: 74% of the $3.48 close cost.
- **What changed:** `vouch/agent/policy.py` `_cache_conversation()` puts one cache marker on the newest user message each turn and clears the older ones (the API allows four markers; the instructions use one). The words sent are identical, so the model sees exactly what it saw before. Only the billing changes.
- **Measured live, not estimated** (two real cases, claude-opus-5-5, effort medium; `scratchpad/measure_case.py`): bank-details case $0.1296 vs $0.2242 for the same calls with instructions-only caching (−42%); paid-after-exit case $0.1546 vs $0.3638 (−58%); together **$0.2842 vs $0.5881 (−52%)**. The "same calls" figure is derived from the API's own token counts for the same run, so the comparison needs no second paid run. A full close is expected to drop from $3.48 to roughly $1.70; this will be measured by a full close at the end, not assumed.
- **Why (track):** "Would you hire it?": cost discipline with no change to behaviour or limits. Accuracy is unchanged by construction.
- **Tests:** `tests/test_x_conversation_cache.py` (3, free): only the newest user message is marked, older markers are cleared, the instructions stay cached, the marker count stays within the API's limit of four, and the words sent are the same apart from the marker.
- **Cost of this check:** $0.28 of live API use.

## 14. 2026-10-24, 20:36 · branch `efficiency` · Phases 2–5, network retry, and two live-agent accuracy fixes

Everything below is measured with the live model unless it says otherwise. Costs are real API charges.

**Phase 2: case brief.** `vouch/agent/brief.py`. Code looks up what the model used to fetch at the start of every case (the full text of each rule the case cites, earlier rulings of its kind, the case's current state) and puts it in the first message. Same tools, so each lookup is audited as "prepared by Vouch"; nothing is summarised. Measured on three cases: 5 model turns each instead of 6–7, about 30% faster per case. It can be switched off (`VOUCH_CASE_BRIEF=0`) for comparisons, and every score records the setting. 4 tests.

**Phase 3: several cases at once.** `ModelPolicy.parallel_cases` (4). The model's thinking runs in parallel; tool calls take turns behind one lock, so database writes, request and question numbers, the hash chain and the evidence list never interleave, and a tool error is counted against its own case only. Decisions are identical to working one at a time (scripted playbook, compared decision by decision). 2 tests. *The speed gain is not yet cleanly measured*: the one 3-run score that finished in 21 minutes ran with the brief on, and the 60-minute one ran while the full test suite was also running, so the two times cannot be compared.

**Phase 4: learning loop.** `vouch/agent/lessons.py`. After a live close, code (free, no model call) turns the agent's own refused or failed steps into proposed lessons about a *kind* of case, with names, IDs and account numbers removed. Only the controller approves (Vouch is refused and logged); an approved lesson joins `lessons/approved.json` and reaches the brief for that kind of case; every score records which lessons it ran with. Lessons are learned only from the main set, never from the scripted playbook. Dashboard: Human review > Lessons. No lesson has been proposed or approved yet. 6 tests.

**Phase 5: selective validation.** `close --record FILE` saves a live run's replies; `close --replay FILE` replays them for free, labelled "replay of <model>" at $0.00 and never a score; a replay asked for something it never recorded stops with a plain reason. `python -m vouch try-case KIND` works one kind of case live on a throwaway copy (the real database is untouched, tested byte for byte) for about a dime. 3 tests.

**Network retry.** A dropped connection, timeout, rate limit or overloaded API (`Unreachable`) no longer holds the case at once: the close waits 10 s, 30 s, then 60 s and works those cases again one at a time, then holds with the plain reason. Real refusals and the spending cap are never retried. The first version retried once, immediately, and the live score showed it was not enough (cases were still lost in 2 of 3 runs). 3 tests.

**Two live-agent accuracy problems the first honest live scores exposed.** A scripted score is 14/14 with 0 false holds, so the planted cases and the scorer are sound. The live model, however, did two things wrong in every run, with the brief on or off, and the earlier live run from before any of this work did the same:
1. *Half-cleared payment batch:* the agent asked Treasury a question as well as requesting funding. The case names 39 people, so the scorer counted all 39 as questioned: 39 false holds in 590 (6.6%, over the 5% limit).
2. *Self-approved accrual:* the agent asked the controller a question instead of sending the accrual request that routes it to the controller, so it was not routed (missed in 3 of 3 runs).
- **Root cause:** the instructions never said how a case's `proposed_action` maps onto the tools, or that a question is for facts the system of record cannot settle. **Fix:** a generic paragraph in the agent's instructions (by kind of action; names no person, payment or planted case): follow the proposed action with the matching tool; when a funding or approval request already reaches the right colleague, do not also ask a question about the same thing; ask only for facts the trusted source cannot settle. It matches what the scripted playbook already does for these actions.
- **Live check, 3 tries each:** half-cleared batch: no question and a funding request to Treasury, 3 of 3 (before: always asked); self-approved accrual: an accrual request to the controller, 3 of 3 (before: never).

**Live scores so far (main set, 3 runs each, claude-opus-5-5):**

| | Brief off | Brief on | Before this PR |
|---|---|---|---|
| Caught | 13/14 | 9/14 | not measured |
| Evidence complete | 14/14 | 10/14 | not measured |
| False holds (worst run) | 6.61% | 6.61% | 6.6% (earlier run) |
| Cost per close | $1.83 | $1.45 | $3.48 |

In the brief-on score every miss except the accrual case was a dropped connection (1 case lost in run 2, 3 in run 3), not a wrong decision, which is what the longer retry addresses. Neither score was run with the accuracy fix above, so neither is the final number. **The final score, with all fixes, is running and will be added here.**

**Why (track):** "Would you hire it?" and "Is it real enough?": cost and speed work that does not trade away accuracy, plus a live score that exposed and fixed real behaviour gaps instead of being tuned. "Asks at the right moment": questions only for facts the sources cannot settle. "Proof it works": every figure above is a measured, labelled result.

**Tests:** 335 passed on the full suite before the retry backoff and the instructions change; 338 passed, 0 failed, 0 skipped on the final code (entry 15). No existing test, answer-key entry, planted case, threshold or seed was changed. Two of my own new tests had wrong expectations, which I corrected because the program was right (recorded in the commit history).

## 15. 2026-10-24, 21:55 · branch `efficiency` · Correction: my first accuracy fix overcorrected; final live score at target

**What went wrong.** Entry 14's instructions paragraph fixed the two problems it targeted (39 false holds; accrual not routed) but the live score that followed showed I had broken other things: the agent skipped questions the cases require (HR on the leaver case, the stock admin on the forfeiture, Treasury on the duplicate payment), and it stopped drafting the backdated-raise correction. Evidence complete fell from 14/14 to 11/14 and one planted mistake went uncaught. Two causes, both mine: (1) "ask only for a fact the system of record cannot settle" told the model to skip confirmations the cases need; (2) the backdated-raise action's name starts with `hold_`, so my "hold, then ask" rule misapplied to it, although it needs a draft plus an arrears request.

**Fix** (`vouch/agent/policy.py`, the agent's instructions, still by kind of action and naming no person, payment, grant, month or planted case): the backdated-raise action gets its own line (draft the correction, request approval for the entry, request approval of kind arrears for the controller); the case's named colleague (`ask_person`) is asked for what only they can confirm, even when the trusted source already points one way; the one exception is a colleague just sent a request, because that request is already the ask. This matches what the scripted playbook does for every kind.

**Checked live before re-scoring** (6 kinds, 2 tries each, $1.03): backdated raise: draft + entry request + arrears request to the controller (2/2); leaver paid: HR asked, entry requested (2/2); forfeiture: stock admin asked, entry requested (2/2); duplicate payment: held, Treasury asked (2/2); half-cleared batch: funding request only, no question (2/2); self-approved accrual: accrual request to the controller (2/2).

**Final live score (main set, 3 runs, claude-opus-5-5, brief on, retry with backoff, all fixes; $4.03):**

| Measure | Result | Target |
|---|---|---|
| Caught | **14/14** | 100% |
| False holds (worst run) | **0/590 (0.00%)** | under 5% |
| Evidence complete | **14/14** | 100% |
| Money correct | **13/13, books balance** | 100% |
| Never (forbidden actions) | **0** | 0 |
| Runs at target | **3/3**, none flaky | |
| Cases lost to dropped connections | **0** | |

**Compared with before (all live, main set, 3 runs):**

| | A: first, brief on, no retry | B: brief off | C: brief on, 1 retry | **Final** |
|---|---|---|---|---|
| Caught | 9/14 | 13/14 | 9/14 | **14/14** |
| Evidence complete | 10/14 | 14/14 | 10/14 | **14/14** |
| False holds | 6.61% | 6.61% | 6.61% | **0%** |
| Cost per close | $1.44 | $1.83 | $1.45 | **$1.34** |

Before any efficiency work, one live close cost **$3.48**. A live close now costs about **$1.34 (−61%)**, and 3 closes take about 13 minutes. A 3-close score took 8 minutes in the run just before this correction and 13 here; the difference is the model's own turn count and the network, so I am not claiming a precise speed figure.

**Not yet shown:** the demo scenario needs **5 runs in a row** at target and has been run for 3 (all 3 at target; the demo check says "not passed" until 5). The scripted playbook was already 14/14. The held-out set has no cases yet and the clean set (no mistakes) has not been scored live. "Handled differently" lists 3 mistakes (MAIN-05, 06, 07): the outcome was right every time, but the exact steps varied between runs.

**Why (track):** "Is it real enough?" and "proof it works": the number that matters moved from 9/14 to 14/14 on a score that exposed my own regression rather than hiding it; "Asks at the right moment": it asks the colleague a case names, and not when a request already is the ask; "Would you hire it?": cost cut by about 60% with accuracy at target.

## 16. 2026-10-25, 03:08 · branch `front-end` (new, from `efficiency`)

**New front end, as a standalone prototype.** `frontend/` (`index.html`, `styles.css`, `app.js`, `README.md`) rebuilds the "Vouch Merged Dashboard" design for the event vendor payables plan: three pages (Overview, Books, Audit trail), the pill navigation, the four colour cards, the decision cards with Approve / Reject / Why? / Undo, and the "What Vouch caught" panel.
- **Not integrated, on purpose:** it calls no backend and reads no database; all content is the design's simulated sample data and every page says so. `vouch/ui` and the backend are unchanged.
- **Set aside:** an earlier start at wiring the new design to the backend (a per-entry balance check in `/api/ledger`, a new shell and styles for `vouch/ui`) is kept in `git stash` ("redesign: wired dashboard start…"), not on any branch.
- **Why (track):** "Would you hire it?" for a manager: one screen that answers what needs a decision, why, and what was caught.
- **Tests:** none; nothing in Python changed. The script passes a syntax check.
