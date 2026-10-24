"""Synthetic bank account numbers in each country's real format, with valid check digits."""
from __future__ import annotations

import random


def us_account(rng: random.Random) -> str:
    """ABA routing number (with its checksum) and an account number: 'routing:account'."""
    while True:
        digits = [rng.randrange(10) for _ in range(8)]
        digits[0] = rng.choice([0, 1, 2])  # Federal Reserve routing symbols start 00-12
        partial = 3 * (digits[0] + digits[3] + digits[6]) + 7 * (digits[1] + digits[4] + digits[7]) + (digits[2] + digits[5])
        check = (10 - partial % 10) % 10
        routing = "".join(map(str, digits + [check]))
        if routing[:2] <= "12":
            break
    account = "".join(str(rng.randrange(10)) for _ in range(rng.choice([9, 10, 12])))
    return f"{routing}:{account}"


def uk_account(rng: random.Random) -> str:
    """Sort code and 8-digit account number: 'NN-NN-NN:NNNNNNNN'."""
    sort_code = "-".join(f"{rng.randrange(100):02d}" for _ in range(3))
    return f"{sort_code}:{rng.randrange(10**8):08d}"


def de_iban(rng: random.Random) -> str:
    """German IBAN: DE + ISO 7064 check digits + 8-digit bank code + 10-digit account."""
    bban = f"{rng.randrange(10**7, 10**8)}{rng.randrange(10**10):010d}"
    numeric = int(bban + "131400")  # 'D'=13, 'E'=14, then '00'
    check = 98 - numeric % 97
    return f"DE{check:02d}{bban}"


def iban_is_valid(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return int(numeric) % 97 == 1


GENERATORS = {"US": us_account, "UK": uk_account, "DE": de_iban}


def new_account(entity: str, rng: random.Random, taken: set[str]) -> str:
    while True:
        account = GENERATORS[entity](rng)
        if account not in taken:
            taken.add(account)
            return account
