"""Rule matching: the six match keys of the old pipeline, exactly."""

from collections.abc import Callable, Mapping
from decimal import Decimal

from moneyflow.core.model import Transaction


def to_decimal(value: object) -> Decimal:
    """Never Decimal(float): a YAML 0.1 must become Decimal('0.1')."""
    return Decimal(str(value))


def _description_contains(value: object, tx: Transaction) -> bool:
    return str(value).lower() in f"{tx.description} {tx.comment}".lower()


def _mcc(value: object, tx: Transaction) -> bool:
    return tx.mcc is not None and tx.mcc == int(str(value))


def _account(value: object, tx: Transaction) -> bool:
    return tx.account_id == value


def _direction(value: object, tx: Transaction) -> bool:
    return tx.direction == value


def _amount_lt(value: object, tx: Transaction) -> bool:
    return tx.amount < to_decimal(value)


def _amount_gte(value: object, tx: Transaction) -> bool:
    return tx.amount >= to_decimal(value)


_KEYS: dict[str, Callable[[object, Transaction], bool]] = {
    "description_contains": _description_contains,
    "mcc": _mcc,
    "account": _account,
    "direction": _direction,
    "amount_lt": _amount_lt,
    "amount_gte": _amount_gte,
}


def matches(match: Mapping[str, object], tx: Transaction) -> bool:
    for key in match:
        if key not in _KEYS:
            raise ValueError(f"unknown match key: {key}")
    return all(_KEYS[key](value, tx) for key, value in match.items())
