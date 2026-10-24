# Vouch

Vouch fills the payroll accountant and equity compensation accountant seats at Northlight Labs, closing payroll and stock grants into the right entries every month, by department and entity.

## Commands

```bash
pip install -e ".[dev]"
python -m vouch.world build          # generate the company world from the fixed seed into ./world
python -m pytest                     # run the tests
python -m vouch.reference.fetch_fx   # daily refresh of recorded exchange rates (needs OPENEXCHANGERATES_API_KEY in .env)
```

## Exchange rates

Rates are recorded daily USD rates from [Open Exchange Rates](https://openexchangerates.org), one per business day, snapshotted in `vouch/reference/fx_oxr_usd.csv`. Builds read only the snapshot, so they work offline and come out the same every time. Each month closes at that month's average rate. A month can't close until every business day in it has a rate (policy FIN-02).

To refresh daily on Windows, run `scripts/register_daily_fx.ps1` once. It registers a scheduled task.
