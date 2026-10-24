"""Calendar helpers shared by every layer. Months are written as 'YYYY-MM'."""
from __future__ import annotations

import calendar
from datetime import date, timedelta


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def parse_month(key: str) -> tuple[int, int]:
    year, month = key.split("-")
    return int(year), int(month)


def add_months(key: str, n: int) -> str:
    year, month = parse_month(key)
    index = year * 12 + (month - 1) + n
    return month_key(index // 12, index % 12 + 1)


def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of month keys from start to end."""
    out, key = [], start
    while key <= end:
        out.append(key)
        key = add_months(key, 1)
    return out


def month_start(key: str) -> date:
    year, month = parse_month(key)
    return date(year, month, 1)


def month_end(key: str) -> date:
    year, month = parse_month(key)
    return date(year, month, calendar.monthrange(year, month)[1])


def days_in_month(key: str) -> int:
    return month_end(key).day


def month_of(d: date) -> str:
    return month_key(d.year, d.month)


def last_business_day(key: str) -> date:
    d = month_end(key)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def nth_weekday(key: str, weekday: int, n: int) -> date:
    """The n-th given weekday (Mon=0) of a month, e.g. the third Thursday."""
    d = month_start(key)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(weeks=n - 1)


def parse_date(text: str) -> date | None:
    return date.fromisoformat(text) if text else None


def overlap_days(start: date, end: date | None, key: str) -> tuple[date, date] | None:
    """The part of [start, end] that falls inside the month, or None."""
    first = max(start, month_start(key))
    last = min(end or month_end(key), month_end(key))
    return (first, last) if first <= last else None
