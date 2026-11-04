"""Synthetic builders shared by the core stage tests."""

from datetime import date, datetime
from decimal import Decimal

from moneyflow.core.model import Account, Inputs, Owner, Person, Rule, Ruling, Transaction


def account(id: str = "acc1", currency: str = "UAH", include: bool = True) -> Account:
    return Account(id=id, name=id, currency=currency, owner="o", role="main", include=include, cash_from=None)


def owner(id: str = "o", budget: bool = True, counterparty_strings: tuple[str, ...] = ()) -> Owner:
    return Owner(id=id, display=id, counterparty_strings=counterparty_strings, budget=budget)


def tx(
    id: str = "t1",
    account_id: str = "acc1",
    amount: str = "10.00",
    currency: str = "UAH",
    direction: str = "expense",
    description: str = "shop",
    day: int = 1,
    source: str = "bank",
) -> Transaction:
    return Transaction(
        id=id,
        account_id=account_id,
        date=date(2026, 1, day),
        time=datetime(2026, 1, day, 12, 0),
        amount=Decimal(amount),
        currency=currency,
        direction=direction,  # type: ignore[arg-type]
        mcc=None,
        description=description,
        comment="",
        source=source,  # type: ignore[arg-type]
    )


def rule(id: str, priority: int, ordinal: int, category: str, subcategory: str | None = None, **match: object) -> Rule:
    return Rule(
        id=id,
        kind="categorize",
        priority=priority,
        ordinal=ordinal,
        match=match,
        result={"category": category, "subcategory": subcategory},
        active=True,
        created_by="test",
    )


def ruling(id: str, transaction_id: str, category: str, subcategory: str | None = None) -> Ruling:
    return Ruling(
        id=id,
        transaction_id=transaction_id,
        kind="category",
        payload={"category": category, "subcategory": subcategory},
        created_by="test",
    )


def exclude_ruling(id: str, transaction_id: str, reason: str) -> Ruling:
    return Ruling(id=id, transaction_id=transaction_id, kind="exclude", payload={"reason": reason, "note": None}, created_by="test")


def white_card_ruling(id: str, transaction_id: str) -> Ruling:
    return Ruling(id=id, transaction_id=transaction_id, kind="white_card", payload={}, created_by="test")


def split_ruling(
    transaction_id: str,
    n: int,
    amount: str,
    category: str,
    subcategory: str | None = None,
    label: str | None = None,
    date: str | None = None,
) -> Ruling:
    payload = {"amount": amount, "category": category, "subcategory": subcategory, "label": label, "date": date}
    return Ruling(id=f"split:{transaction_id}:{n}", transaction_id=transaction_id, kind="split", payload=payload, created_by="test")


def inputs(
    transactions: tuple[Transaction, ...] = (),
    rules: tuple[Rule, ...] = (),
    rulings: tuple[Ruling, ...] = (),
    accounts: tuple[Account, ...] = (),
    categories: frozenset[str] = frozenset({"food", "misc"}),
    owners: tuple[Owner, ...] = (),
    people: tuple[Person, ...] = (),
) -> Inputs:
    return Inputs(
        owners=owners or (owner(),),
        accounts=accounts or (account(),),
        transactions=transactions,
        rules=rules,
        rulings=rulings,
        categories=categories,
        people=people,
    )
