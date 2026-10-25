# Vouch

Vouch is the event tax accountant on Nordlicht's finance team: it decides the German tax treatment of every company event, from deductibility and VAT to employees' taxable benefits, keeps every person's yearly totals, and never files or pays anything without approval.

Nordlicht and its people, invoices, and events are simulated. Calculations implement
VOUCH_DE_EVENT_TAX_SPEC.md with exact Decimal arithmetic. Company policies are labelled POL-*.
The agent gathers facts and selects permitted actions; code owns the calculations and controls.

## Run the connected UI

```powershell
python -m pip install -e ".[dev]"
python -m vouch api
```

Open http://127.0.0.1:8000. Choose a public scenario and scripted or live mode. The frontend was
imported from origin/frontend; its design is retained and its sample figures have been replaced
with API results. Each run has its own database and replayable timeline. Human approval requires
a reason. Approval records a review; filing, paying tax, and running payroll remain outside the agent.

## Process a scenario from the CLI

```powershell
python -m vouch rebuild --case cases/public/P11.yaml --db demo.db
python -m vouch run --case cases/public/P11.yaml --db demo.db --scripted
python -m vouch run --case cases/public/P11.yaml --db demo.db --model claude-opus-5-5 --effort medium
python -m vouch why EVT-SOM26 --db demo.db
python -m vouch.controls --db demo.db verify
```

Rebuild the scenario database before an independent run. The API and scorer isolate their own runs.
`python -m vouch rebuild` rebuilds the fixed base company. Every scenario supplies an as_of date;
future invoices, attendance, entries, and elections are hidden by the loader.

## Models and credentials

Put ANTHROPIC_API_KEY and/or OPENAI_API_KEY in the local .env file or process environment.
The .env file is ignored by Git. Live reports identify the actual model and usage.

- Claude: claude-opus-5-5; claude-sonnet-5-5 is also selectable.
- GPT: gpt-6.1-sol through the Responses API, with the same typed tools and controls.
- Live CLI/API/scorer runs use medium effort by default, as requested. Override with --effort.

GPT is an additional model option; no equivalence to Claude is claimed without task-specific results.
Model/tool integration follows [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)
and the [GPT-6.1 Sol model contract](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

## Tests, scores, and benchmarks

```powershell
python -m pytest                         # full suite, including billed live API tests
python -m pytest -m "not live"           # explicitly selected deterministic development checks
python -m pytest -m live                 # real agent: public cases 3 times, P11 5 times
python -m vouch score --scripted --runs 3
python -m vouch score --model claude-opus-5-5 --effort medium --runs 3 --concurrency 6
python -m vouch score --model gpt-6.1-sol --effort medium --runs 3
python -m vouch bench --output perf_baseline.json
```

Live tests fail explicitly on missing credentials or API errors; they are not silently skipped.
`VOUCH_MODEL` selects the model for a test process. Reports are saved under results/ and always
label scripted/model mode, model name, date, run count, measured usage, and failures.
The scorer alone reads expected answers; agent Scenario objects do not contain expect blocks.
The optional owner holdout is loaded only by the scorer from VOUCH_HOLDOUT_PATH and reports
aggregates by default. Sealed legacy answers are not used by the event-tax agent.

See [nergis_DOC.md](nergis_DOC.md) for task-by-task plain-language and technical reports,
[FIX_LOG.md](FIX_LOG.md) for root-cause fixes, and [PERF.md](PERF.md) for measured performance
and unmet validation gates. The current task status is in [PLAN.md](PLAN.md).
