"""Fixed parameters of the Northlight Labs world. Changing any value changes every file."""
from __future__ import annotations

from decimal import Decimal

SEED = 20261023

COMPANY = "Northlight Labs"
DOMAIN = "northlightlabs.com"
FOUNDED = "2021-01"

# Payroll is computed from the start of the earliest tax year the files touch, so
# year-to-date caps are right; files are only written from FIRST_FILE_MONTH on.
PAYROLL_START = "2025-01"
FIRST_FILE_MONTH = "2025-09"
OPEN_MONTH = "2026-09"          # 12 closed months (2025-09 .. 2026-08) + this one

ENTITIES = {
    "US": {"legal_name": "Northlight Labs Inc.", "country": "United States", "currency": "USD", "city": "New York"},
    "UK": {"legal_name": "Northlight Labs Ltd.", "country": "United Kingdom", "currency": "GBP", "city": "London"},
    "DE": {"legal_name": "Northlight Labs GmbH", "country": "Germany", "currency": "EUR", "city": "Berlin"},
}
ENTITY_WEIGHTS = {"US": 50, "UK": 25, "DE": 25}

DEPARTMENTS = ["Engineering", "Product", "GTM", "G&A"]
DEPARTMENT_WEIGHTS = {"Engineering": 45, "Product": 15, "GTM": 25, "G&A": 15}

LEVEL_WEIGHTS = {1: 20, 2: 30, 3: 25, 4: 14, 5: 8, 6: 3}

# Annual base salary for a US engineer by level; other cells scale from it.
US_ENGINEERING_BASE = {1: 105_000, 2: 130_000, 3: 160_000, 4: 195_000, 5: 235_000, 6: 285_000}
ENTITY_SALARY_FACTOR = {"US": Decimal("1.00"), "UK": Decimal("0.55"), "DE": Decimal("0.62")}
DEPARTMENT_SALARY_FACTOR = {"Engineering": Decimal("1.00"), "Product": Decimal("0.98"),
                            "GTM": Decimal("0.88"), "G&A": Decimal("0.82")}

START_HEADCOUNT = 45
TARGET_HEADCOUNT = 300
MONTHLY_EXIT_RATE = 0.0085
MONTHLY_TRANSFER_RATE = 0.002

GRANT_SHARE_RATE = 0.85        # share of hires who receive a stock grant
GRANT_SHARES_BY_LEVEL = {1: 1_500, 2: 3_000, 3: 6_000, 4: 12_000, 5: 25_000, 6: 50_000}
VEST_MONTHS = 48
CLIFF_MONTHS = 12
BOARD_MEETING_MONTHS = (2, 5, 8, 11)   # third Thursday of each

# Grant-date fair value per share in USD, by quarter since founding (409A-style step-up).
FAIR_VALUE_START = Decimal("0.40")
FAIR_VALUE_QUARTERLY_GROWTH = Decimal("1.13")

# Finance colleagues who appear in documents and approve things.
DIRECTORY = [
    {"person_id": "P-CTRL", "name": "Amara Okafor", "role": "controller", "title": "Controller"},
    {"person_id": "P-FINM", "name": "Daniel Reyes", "role": "finance_manager", "title": "Finance Manager"},
    {"person_id": "P-HR", "name": "Priya Natarajan", "role": "hr", "title": "Head of People Operations"},
    {"person_id": "P-TRSY", "name": "Lena Fischer", "role": "treasury", "title": "Treasury Lead"},
    {"person_id": "P-STCK", "name": "Marcus Bell", "role": "stock_admin", "title": "Stock Plan Administrator"},
    {"person_id": "P-ENG", "name": "Sofia Lindqvist", "role": "department_head", "department": "Engineering", "title": "VP Engineering"},
    {"person_id": "P-PRD", "name": "Kenji Watanabe", "role": "department_head", "department": "Product", "title": "VP Product"},
    {"person_id": "P-GTM", "name": "Rachel Moreno", "role": "department_head", "department": "GTM", "title": "VP Go-To-Market"},
    {"person_id": "P-GNA", "name": "Olu Adeyemi", "role": "department_head", "department": "G&A", "title": "VP Operations"},
]

FIRST_NAMES = {
    "US": ["James", "Maria", "Michael", "Jennifer", "David", "Linda", "Chris", "Ashley", "Brian", "Jessica",
           "Kevin", "Amanda", "Jason", "Megan", "Ryan", "Lauren", "Eric", "Rachel", "Tyler", "Nicole",
           "Andre", "Keisha", "Luis", "Camila", "Wei", "Mei", "Raj", "Ananya", "Omar", "Leila"],
    "UK": ["Oliver", "Amelia", "Harry", "Isla", "George", "Ava", "Jack", "Emily", "Charlie", "Sophie",
           "Thomas", "Grace", "Alfie", "Freya", "Rhys", "Niamh", "Callum", "Eilidh", "Arjun", "Zara",
           "Kwame", "Imani", "Hamza", "Aisha", "Finn", "Poppy", "Lewis", "Ellie", "Rory", "Holly"],
    "DE": ["Lukas", "Mia", "Leon", "Emma", "Finn", "Hannah", "Jonas", "Lea", "Paul", "Lina",
           "Felix", "Marie", "Maximilian", "Sophia", "Elias", "Clara", "Noah", "Anna", "Moritz", "Johanna",
           "Emir", "Elif", "Mehmet", "Ayse", "Jan", "Katharina", "Tobias", "Laura", "Niklas", "Sarah"],
}
LAST_NAMES = {
    "US": ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
           "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
           "Thompson", "White", "Harris", "Clark", "Lewis", "Robinson", "Walker", "Young", "Chen", "Patel"],
    "UK": ["Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Robinson", "Wright",
           "Thompson", "Evans", "Walker", "White", "Roberts", "Green", "Hall", "Wood", "Jackson", "Clarke",
           "Hughes", "Edwards", "Khan", "Ali", "Singh", "Murphy", "Campbell", "Stewart", "Owen", "Price"],
    "DE": ["Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Schulz", "Hoffmann",
           "Schäfer", "Koch", "Bauer", "Richter", "Klein", "Wolf", "Schröder", "Neumann", "Schwarz", "Zimmermann",
           "Braun", "Krüger", "Hofmann", "Hartmann", "Lange", "Schmitt", "Werner", "Yilmaz", "Kaya", "Krause"],
}
