import pytest

from moneyflow.core.model import Account


def test_account_requires_cash_from():
    with pytest.raises(TypeError):
        Account(id="a", name="a", currency="UAH", owner="o", role="main", include=True)  # type: ignore[call-arg]
