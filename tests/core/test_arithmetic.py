from decimal import Decimal

from moneyflow.core.build import build
from tests.core._builders import inputs, tx


def test_expense_is_negative_income_positive():
    inp = inputs(
        transactions=(
            tx("e", amount="5.00", direction="expense"),
            tx("i", amount="7.00", direction="income"),
        )
    )
    by_id = {e.transaction_id: e.amount for e in build(inp)}
    assert by_id == {"e": Decimal("-5.00"), "i": Decimal("7.00")}
