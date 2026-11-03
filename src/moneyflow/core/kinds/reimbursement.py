"""The reimbursement ruling kind (A18): money that came back against an expense category."""

from moneyflow.core.kinds._fields import _text
from moneyflow.core.model import Ruling


def category(ruling: Ruling) -> str:
    return _text(ruling.payload, "category", f"reimbursement ruling {ruling.id}", ruling.transaction_id)
