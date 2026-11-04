"""The debt ruling kind (A19): money lent to or repaid by a person, booked under `Debts`.

Ported from the old `apply_debts` and `check_debts`, the checks one build can see: UAH only, kind
matches the bank direction, known person, running balance never below zero. Not ported: "no Debts
row without an entry" (a Debts category can now only come from a debt ruling or a categorize rule,
and a categorize rule on a kept entry is not a debt check), "outside every budget cycle" (0b.3, once cycles exist).
"""

from decimal import Decimal

from moneyflow.core.kinds._fields import _text
from moneyflow.core.model import BuildRefused, Person, Ruling, Transaction

CATEGORY = "Debts"
WANTED_DIRECTION = {"lent": "expense", "repaid": "income"}

Booked = tuple[Person, str, Transaction]


def parse(ruling: Ruling, people: dict[str, Person]) -> tuple[Person, str]:
    """(person, kind) of a debt ruling, or a refusal for an unknown person or kind."""
    where = f"debt ruling {ruling.id}"
    person_id = _text(ruling.payload, "person", where, ruling.transaction_id)
    kind = _text(ruling.payload, "kind", where, ruling.transaction_id)
    if person_id not in people:
        raise BuildRefused(f"{where}: unknown person {person_id}", ruling.transaction_id)
    if kind not in WANTED_DIRECTION:
        raise BuildRefused(f"{where}: kind {kind} is not lent or repaid", ruling.transaction_id)
    return people[person_id], kind


def check_row(tx: Transaction, kind: str) -> None:
    if tx.currency != "UAH":
        raise BuildRefused(f"debt on a {tx.currency} transaction, debts are UAH only", tx.id)
    if tx.direction != WANTED_DIRECTION[kind]:
        raise BuildRefused(f"debt kind {kind} needs a {WANTED_DIRECTION[kind]} transaction, this one is {tx.direction}", tx.id)


def check_balances(booked: list[Booked]) -> None:
    """Per person in time order from `opening_balance`: lent adds, repaid subtracts, never below 0."""
    balance: dict[str, Decimal] = {}
    for person, kind, tx in sorted(booked, key=lambda b: (b[2].time, b[2].id)):
        current = balance.get(person.id, person.opening_balance)
        balance[person.id] = current + tx.amount if kind == "lent" else current - tx.amount
        if balance[person.id] < 0:
            raise BuildRefused(f"repaid is more than lent plus the opening balance for {person.id}", tx.id)
