"""Core data model: frozen dataclasses, every field required, no defaults."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Owner:
    id: str
    display: str
    counterparty_strings: tuple[str, ...]
    budget: bool


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    currency: str
    owner: str
    role: str
    include: bool
    cash_from: date | None  # non-null marks the cash account and its cutover date


@dataclass(frozen=True)
class Transaction:
    id: str
    account_id: str
    date: date
    time: datetime
    amount: Decimal  # positive, as in the bank export
    currency: str
    direction: Literal["income", "expense"]
    mcc: int | None
    description: str
    comment: str


@dataclass(frozen=True)
class Rule:
    id: str
    kind: Literal["categorize", "exclude"]
    priority: int
    ordinal: int  # position in the source file
    match: Mapping[str, object]
    result: Mapping[str, str | bool | None]  # categorize: category, subcategory; exclude: reason, optional paired
    active: bool
    created_by: str


@dataclass(frozen=True)
class Ruling:
    id: str
    transaction_id: str
    kind: Literal["category", "exclude", "white_card"]
    payload: Mapping[str, str | None]  # category: category, subcategory, note; exclude: reason, note; white_card: empty
    created_by: str


@dataclass(frozen=True)
class LedgerEntry:
    transaction_id: str
    part: int
    date: date
    account_id: str
    category: str | None
    subcategory: str | None
    amount: Decimal  # signed: income +, expense -
    currency: str
    status: Literal["kept", "excluded", "unresolved"]
    reason: str | None
    decided_by: str | None


@dataclass(frozen=True)
class Inputs:
    owners: tuple[Owner, ...]
    accounts: tuple[Account, ...]
    transactions: tuple[Transaction, ...]
    rules: tuple[Rule, ...]
    rulings: tuple[Ruling, ...]
    categories: frozenset[str]


Ledger = tuple[LedgerEntry, ...]


class BuildRefused(Exception):
    def __init__(self, reason: str, transaction_id: str | None):
        super().__init__(reason if transaction_id is None else f"{reason} (transaction {transaction_id})")
        self.reason = reason
        self.transaction_id = transaction_id
