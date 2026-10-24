"""One-off fetch of recorded USD exchange rates from Open Exchange Rates.

Run:  python -m vouch.reference.fetch_fx              # daily refresh: every completed day since the last one saved
      python -m vouch.reference.fetch_fx start [end]  # backfill a range

Reads OPENEXCHANGERATES_API_KEY from the environment or .env. Writes one row per
weekday to fx_oxr_usd.csv next to this file. Dates already in the file are skipped,
so a re-run only spends API calls on missing days. Today is never fetched because
its rate is not final until the day ends. Builds read the snapshot and never call
the API, which keeps the world reproducible offline.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

SNAPSHOT = Path(__file__).with_name("fx_oxr_usd.csv")
CURRENCIES = ("GBP", "EUR")
SOURCE = "https://openexchangerates.org/api/historical/{day}.json"


def _api_key() -> str:
    key = os.environ.get("OPENEXCHANGERATES_API_KEY")
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENEXCHANGERATES_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("OPENEXCHANGERATES_API_KEY is not set (environment or .env)")
    return key


def _read_snapshot() -> dict[str, dict[str, str]]:
    if not SNAPSHOT.exists():
        return {}
    with SNAPSHOT.open(newline="", encoding="utf-8") as f:
        return {row["date"]: row for row in csv.DictReader(f)}


def _write_snapshot(rows: dict[str, dict[str, str]]) -> None:
    with SNAPSHOT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "base", *CURRENCIES, "source", "timestamp"], lineterminator="\n")
        writer.writeheader()
        for day in sorted(rows):
            writer.writerow(rows[day])


def fetch(start: date, end: date) -> None:
    key = _api_key()
    rows = _read_snapshot()
    day, fetched = start, 0
    try:
        while day <= end:
            iso = day.isoformat()
            if day.weekday() < 5 and iso not in rows:
                url = SOURCE.format(day=iso) + "?app_id=" + key
                with urllib.request.urlopen(url, timeout=30) as response:
                    payload = json.load(response)
                rows[iso] = {"date": iso, "base": payload["base"],
                             **{c: str(payload["rates"][c]) for c in CURRENCIES},
                             "source": SOURCE.format(day=iso), "timestamp": str(payload["timestamp"])}
                fetched += 1
            day += timedelta(days=1)
    finally:
        _write_snapshot(rows)
        print(f"fetched {fetched} new days; snapshot has {len(rows)} days -> {SNAPSHOT.name}")


def refresh() -> None:
    """Fetch every completed weekday after the last one in the snapshot."""
    rows = _read_snapshot()
    start = date.fromisoformat(max(rows)) + timedelta(days=1) if rows else date(2025, 9, 1)
    fetch(start, date.today() - timedelta(days=1))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        refresh()
    else:
        yesterday = date.today() - timedelta(days=1)
        end = date.fromisoformat(args[1]) if len(args) > 1 else yesterday
        fetch(date.fromisoformat(args[0]), min(end, yesterday))
