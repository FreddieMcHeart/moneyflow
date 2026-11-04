from datetime import date
from decimal import Decimal

import pytest

from moneyflow.core.model import Account, Inputs


def test_account_requires_cash_from():
    with pytest.raises(TypeError):
        Account(id="a", name="a", currency="UAH", owner="o", role="main", include=True)  # type: ignore[call-arg]


def test_account_requires_fx_floor_step_and_cash_opening():
    base = dict(id="a", name="a", currency="UAH", owner="o", role="main", include=True, cash_from=None)
    with pytest.raises(TypeError):
        Account(**base, cash_opening=None)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Account(**base, fx_income_floor_step=Decimal("5"))  # type: ignore[call-arg]


def test_inputs_requires_fx_rates_and_today():
    base = dict(owners=(), accounts=(), transactions=(), rules=(), rulings=(), categories=frozenset(), people=())
    with pytest.raises(TypeError):
        Inputs(**base, today=date(2026, 1, 1))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Inputs(**base, fx_rates=())  # type: ignore[call-arg]
