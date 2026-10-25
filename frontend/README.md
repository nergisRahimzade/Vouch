# Vouch frontend

Imported from `origin/frontend` (prototype commit `0480155`) and connected to the German event-tax API.
The original visual design and CSS are retained. Displayed amounts, approvals, entries, and audit rows
come from actual API results; the earlier vendor-payables sample figures have been removed.

Run `python -m vouch api` and open http://127.0.0.1:8000. Choose a public scenario and run mode.
Scripted demonstrations are explicitly labelled. Approval buttons record a human review with a required
reason; they do not file, pay, or run payroll. Do not open index.html directly: it requires the API.
