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
    fx_income_floor_step: Decimal  # floor step for a USD income row on this account (5 unless the FOP account)
    cash_opening: Decimal | None  # opening cash balance; set only on the account whose cash_from is set


@dataclass(frozen=True)
class FxRate:
    id: str
    date: date
    rate: Decimal
    kind: Literal["sell", "official", "effective", "midpoint"]
    method: str


@dataclass(frozen=True)
class Person:
    id: str
    display: str
    opening_balance: Decimal
    note: str | None


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
    source: Literal["bank", "manual"]


@dataclass(frozen=True)
class Rule:
    id: str
    kind: Literal[
        "categorize", "exclude", "credit_repayment", "cancellation", "person_return", "investment_withdrawal"
    ]
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
    kind: Literal["category", "exclude", "white_card", "split", "reimbursement", "debt", "attribute"]
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
    amount: Decimal  # signed: expense -, income and reimbursement +
    currency: str
    status: Literal["kept", "excluded", "unresolved", "outside_cycle"]
    reason: str | None
    decided_by: str | None
    direction: Literal["income", "expense", "reimbursement"]  # set by ingest, changed only by reclassify
    via: str | None  # the reclassify rule or ruling that changed the row: rule:<id> / ruling:<id>
    budget_month: str | None  # "YYYY-MM" of the cycle the row falls in
    amount_uah: Decimal | None  # signed UAH value; None until converted
    rate: Decimal | None  # UAH per unit for a converted foreign-currency row
    rate_kind: str | None


@dataclass(frozen=True)
class Inputs:
    owners: tuple[Owner, ...]
    accounts: tuple[Account, ...]
    transactions: tuple[Transaction, ...]
    rules: tuple[Rule, ...]
    rulings: tuple[Ruling, ...]
    categories: frozenset[str]
    people: tuple[Person, ...]
    fx_rates: tuple[FxRate, ...]
    today: date  # filled by services from the clock; core never reads one


Ledger = tuple[LedgerEntry, ...]


class BuildRefused(Exception):
    def __init__(self, reason: str, transaction_id: str | None):
        super().__init__(reason if transaction_id is None else f"{reason} (transaction {transaction_id})")
        self.reason = reason
        self.transaction_id = transaction_id
