"""Synthetic builders shared by the core stage tests."""

from datetime import date, datetime
from decimal import Decimal

from moneyflow.core.model import Account, Inputs, Rule, Ruling, Transaction


def account(id: str = "acc1", currency: str = "UAH", include: bool = True) -> Account:
    return Account(id=id, name=id, currency=currency, owner="o", role="main", include=include)


def tx(
    id: str = "t1",
    account_id: str = "acc1",
    amount: str = "10.00",
    currency: str = "UAH",
    direction: str = "expense",
    description: str = "shop",
    day: int = 1,
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


def inputs(
    transactions: tuple[Transaction, ...] = (),
    rules: tuple[Rule, ...] = (),
    rulings: tuple[Ruling, ...] = (),
    accounts: tuple[Account, ...] = (),
    categories: frozenset[str] = frozenset({"food", "misc"}),
) -> Inputs:
    return Inputs(
        accounts=accounts or (account(),),
        transactions=transactions,
        rules=rules,
        rulings=rulings,
        categories=categories,
    )
