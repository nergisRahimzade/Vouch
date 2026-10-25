"""Deterministic people for the base company (Appendix A): employees E001–E450 named with Faker de_DE
(seed 42), cost centers from the member ranges, managers from the list; business partners BP-001–BP-600
named with Faker de_DE (seed 7) and placed at one of 80 fictional customer companies, with the named
partners taking their given names. The Faker version is pinned in pyproject.toml so the names never move."""
from __future__ import annotations

import re

from faker import Faker

RANGE = re.compile(r"^\s*([A-Z]+-?)(\d+)\s*-\s*(?:[A-Z]+-?)?(\d+)\s*$")


def expand(spec) -> list[str]:
    """'E001-E120' -> E001..E120; 'BP-001-BP-600' -> BP-001..BP-600; a list or a single id passes through."""
    if spec is None or spec == "":
        return []
    if isinstance(spec, list):
        return [i for item in spec for i in expand(item)]
    match = RANGE.match(str(spec))
    if not match:
        return [str(spec).strip()]
    prefix, first, last = match.group(1), match.group(2), match.group(3)
    width = len(first)
    return [f"{prefix}{n:0{width}d}" for n in range(int(first), int(last) + 1)]


def employees(spec: dict, cost_centers: list[dict]) -> list[dict]:
    faker = Faker("de_DE")
    faker.seed_instance(int(re.search(r"seed\s+(\d+)", spec["generator"]["names"]).group(1)))
    members = {e: cc["id"] for cc in cost_centers for e in expand(cc.get("members"))}
    managers = set(spec.get("managers") or [])
    return [{"id": e, "name": faker.name(), "cost_center": members.get(e), "is_manager": int(e in managers)}
            for e in expand(spec["generator"]["ids"])]


def business_partners(spec: dict) -> list[dict]:
    faker = Faker("de_DE")
    faker.seed_instance(int(re.search(r"seed\s+(\d+)", spec["generator"]["names"]).group(1)))
    pool_size = int(re.search(r"(\d+)", spec["generator"].get("companies", "80")).group(1))
    companies = []
    while len(companies) < pool_size:
        name = faker.company()
        if name not in companies:
            companies.append(name)
    named = {p["id"]: p for p in spec.get("named") or []}
    out = []
    for n, partner in enumerate(expand(spec["generator"]["ids"])):
        name, company = faker.name(), companies[n % pool_size]
        out.append({"id": partner, "name": named.get(partner, {}).get("name", name),
                    "company": named.get(partner, {}).get("company", company)})
    return out
