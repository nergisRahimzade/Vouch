"""Simulates Northlight Labs month by month from founding: hires, raises, exits,
entity transfers and stock grants. The result is the ground truth the source files
are later drawn from (each source can then be planted with a mistake)."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from vouch import dates
from vouch.world import config as C
from vouch.world.banking import new_account


@dataclass
class Stint:
    """A continuous period of employment in one entity."""
    entity: str
    start: date
    end: date | None          # last day in this entity, inclusive
    bank_account: str


@dataclass
class SalaryChange:
    change_id: str
    employee_id: str
    effective_date: date
    annual_salary: Decimal
    currency: str
    previous_salary: Decimal | None
    reason: str               # hire | annual_review | promotion | entity_transfer
    level: int
    approved_by: str
    approved_on: date


@dataclass
class Person:
    employee_id: str
    first_name: str
    last_name: str
    department: str
    level: int
    hire_date: date
    exit_date: date | None = None
    stints: list[Stint] = field(default_factory=list)
    salaries: list[SalaryChange] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def email(self) -> str:
        return f"{self.employee_id.lower().replace('-', '')}@{C.DOMAIN}"

    def employed_on(self, d: date) -> bool:
        return self.hire_date <= d and (self.exit_date is None or d <= self.exit_date)

    def stint_on(self, d: date) -> Stint:
        for stint in self.stints:
            if stint.start <= d and (stint.end is None or d <= stint.end):
                return stint
        return self.stints[-1] if d > self.stints[-1].start else self.stints[0]

    def salary_on(self, d: date) -> SalaryChange:
        current = self.salaries[0]
        for change in self.salaries:
            if change.effective_date <= d:
                current = change
        return current

    def level_on(self, d: date) -> int:
        return self.salary_on(d).level


@dataclass
class Grant:
    grant_id: str
    employee_id: str
    shares: int
    fair_value_per_share: Decimal | None   # USD; set at grant date (board approval)
    vest_start: date
    vest_months: int
    cliff_months: int
    grant_date: date | None = None         # board approval date = ASC 718 grant date
    board_resolution: str | None = None
    signed_on: date | None = None


@dataclass
class BoardMeeting:
    resolution_id: str
    meeting_date: date
    grant_ids: list[str]


@dataclass
class World:
    people: list[Person]
    grants: list[Grant]
    board_meetings: list[BoardMeeting]
    salary_changes: list[SalaryChange]
    bank_accounts_taken: set[str]

    def person(self, employee_id: str) -> Person:
        return self._index()[employee_id]

    def _index(self) -> dict[str, Person]:
        if not hasattr(self, "_by_id"):
            self._by_id = {p.employee_id: p for p in self.people}
        return self._by_id


def _weighted(rng: random.Random, weights: dict):
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def band_salary(entity: str, department: str, level: int, rng: random.Random) -> Decimal:
    base = Decimal(C.US_ENGINEERING_BASE[level]) * C.ENTITY_SALARY_FACTOR[entity] * C.DEPARTMENT_SALARY_FACTOR[department]
    jitter = Decimal(rng.randint(900, 1100)) / 1000
    return (base * jitter / 500).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 500


def fair_value_on(d: date) -> Decimal:
    founded = dates.month_start(C.FOUNDED)
    quarter = ((d.year - founded.year) * 12 + d.month - founded.month) // 3
    return (C.FAIR_VALUE_START * C.FAIR_VALUE_QUARTERLY_GROWTH ** quarter).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _department_head(department: str) -> str:
    return next(d["name"] for d in C.DIRECTORY if d.get("department") == department)


class _Simulator:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.people: list[Person] = []
        self.grants: list[Grant] = []
        self.meetings: list[BoardMeeting] = []
        self.changes: list[SalaryChange] = []
        self.accounts: set[str] = set()
        self.names: set[str] = set()

    # -- helpers -------------------------------------------------------------
    def _change(self, person: Person, effective: date, salary: Decimal, entity: str, reason: str) -> None:
        previous = person.salaries[-1].annual_salary if person.salaries else None
        approver = C.DIRECTORY[1]["name"] if reason == "entity_transfer" else _department_head(person.department)
        change = SalaryChange(
            change_id=f"SC-{len(self.changes) + 1:05d}", employee_id=person.employee_id,
            effective_date=effective, annual_salary=salary, currency=C.ENTITIES[entity]["currency"],
            previous_salary=previous, reason=reason, level=person.level, approved_by=approver,
            approved_on=effective - timedelta(days=self.rng.randint(5, 20)))
        person.salaries.append(change)
        self.changes.append(change)

    def _unique_name(self, entity: str) -> tuple[str, str]:
        while True:
            first, last = self.rng.choice(C.FIRST_NAMES[entity]), self.rng.choice(C.LAST_NAMES[entity])
            if f"{first} {last}" not in self.names:
                self.names.add(f"{first} {last}")
                return first, last

    def _active(self, d: date) -> list[Person]:
        return [p for p in self.people if p.employed_on(d)]

    # -- monthly events ------------------------------------------------------
    def hire(self, key: str) -> None:
        entity = _weighted(self.rng, C.ENTITY_WEIGHTS)
        department = _weighted(self.rng, C.DEPARTMENT_WEIGHTS)
        level = _weighted(self.rng, C.LEVEL_WEIGHTS)
        start = dates.month_start(key) if self.rng.random() < 0.5 else \
            dates.month_start(key) + timedelta(days=self.rng.randint(1, dates.days_in_month(key) - 1))
        first, last = self._unique_name(entity)
        person = Person(f"NL-{len(self.people) + 1:05d}", first, last, department, level, start)
        person.stints.append(Stint(entity, start, None, new_account(entity, self.rng, self.accounts)))
        self._change(person, start, band_salary(entity, department, level, self.rng), entity, "hire")
        self.people.append(person)
        if self.rng.random() < C.GRANT_SHARE_RATE:
            shares = int(C.GRANT_SHARES_BY_LEVEL[level] * self.rng.uniform(0.8, 1.2) / 100) * 100
            self.grants.append(Grant(f"G-{len(self.grants) + 1:05d}", person.employee_id, shares, None,
                                     start, C.VEST_MONTHS, C.CLIFF_MONTHS))

    def exits(self, key: str) -> None:
        first = dates.month_start(key)
        for person in self._active(first):
            if person.hire_date < first and self.rng.random() < C.MONTHLY_EXIT_RATE:
                person.exit_date = first + timedelta(days=self.rng.randint(0, dates.days_in_month(key) - 1))
                person.stints[-1].end = person.exit_date

    def transfers(self, key: str) -> None:
        first = dates.month_start(key)
        for person in self._active(first):
            tenure_months = (first.year - person.hire_date.year) * 12 + first.month - person.hire_date.month
            if tenure_months < 12 or person.exit_date or self.rng.random() >= C.MONTHLY_TRANSFER_RATE:
                continue
            old = person.stints[-1]
            new_entity = self.rng.choice([e for e in C.ENTITIES if e != old.entity])
            move = first if self.rng.random() < 0.6 else first + timedelta(days=self.rng.randint(9, 19))
            old.end = move - timedelta(days=1)
            person.stints.append(Stint(new_entity, move, None, new_account(new_entity, self.rng, self.accounts)))
            self._change(person, move, band_salary(new_entity, person.department, person.level, self.rng),
                         new_entity, "entity_transfer")

    def annual_review(self, key: str) -> None:
        effective = dates.month_start(key)
        cutoff = date(effective.year - 1, 10, 1)
        for person in self._active(effective):
            if person.hire_date >= cutoff or person.exit_date:
                continue
            current = person.salary_on(effective)
            raise_pct = Decimal(self.rng.randint(20, 60)) / 1000
            reason = "annual_review"
            if person.level < 6 and self.rng.random() < 0.12:
                person.level += 1
                raise_pct += Decimal(self.rng.randint(80, 120)) / 1000
                reason = "promotion"
            salary = (current.annual_salary * (1 + raise_pct) / 500).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 500
            self._change(person, effective, salary, person.stint_on(effective).entity, reason)

    def board_meeting(self, key: str) -> None:
        meeting = dates.nth_weekday(key, 3, 3)   # third Thursday
        pending = [g for g in self.grants if g.grant_date is None and g.vest_start <= meeting]
        resolution = f"BR-{key}"
        for grant in pending:
            grant.grant_date = meeting
            grant.board_resolution = resolution
            grant.fair_value_per_share = fair_value_on(meeting)
            grant.signed_on = meeting + timedelta(days=self.rng.randint(3, 14))
        self.meetings.append(BoardMeeting(resolution, meeting, [g.grant_id for g in pending]))

    def run(self) -> World:
        months = dates.month_range(C.FOUNDED, C.OPEN_MONTH)
        for _ in range(C.START_HEADCOUNT):
            self.hire(C.FOUNDED)
        for index, key in enumerate(months):
            if index > 0:
                self.exits(key)
                self.transfers(key)
                if dates.parse_month(key)[1] == 4:
                    self.annual_review(key)
                target = C.START_HEADCOUNT + (C.TARGET_HEADCOUNT - C.START_HEADCOUNT) * index / (len(months) - 1)
                active = len(self._active(dates.month_end(key)))
                for _ in range(max(0, round(target - active + self.rng.uniform(-1.5, 1.5)))):
                    self.hire(key)
            if dates.parse_month(key)[1] in C.BOARD_MEETING_MONTHS:
                self.board_meeting(key)
        return World(self.people, self.grants, self.meetings, self.changes, self.accounts)


def simulate(seed: int = C.SEED) -> World:
    return _Simulator(random.Random(seed)).run()
