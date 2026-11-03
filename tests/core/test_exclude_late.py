"""A17: an Investments row on a non-budget owner's account is excluded after categorize. Synthetic data only."""

from datetime import date
from decimal import Decimal

from moneyflow.core.model import Account, LedgerEntry, Owner
from moneyflow.core.stages.exclude_late import exclude_late
from tests.core._builders import inputs

OWNERS = (
    Owner(id="a", display="a", counterparty_strings=(), budget=True),
    Owner(id="b", display="b", counterparty_strings=(), budget=False),
)
ACCOUNTS = (
    Account(id="mine", name="mine", currency="UAH", owner="a", role="main", include=True, cash_from=None),
    Account(id="theirs", name="theirs", currency="UAH", owner="b", role="main", include=True, cash_from=None),
)


def entry(account_id: str, category: str | None, status: str = "kept") -> LedgerEntry:
    return LedgerEntry(
        transaction_id="t1",
        part=0,
        date=date(2026, 1, 1),
        account_id=account_id,
        category=category,
        subcategory=None,
        amount=Decimal("-10.00"),
        currency="UAH",
        status=status,  # type: ignore[arg-type]
        reason=None,
        decided_by="rule:r1" if status == "kept" else None,
        direction="expense",
        via=None,
    )


def run(e: LedgerEntry) -> LedgerEntry:
    (out,) = exclude_late([e], inputs(owners=OWNERS, accounts=ACCOUNTS))
    return out


def test_other_owner_investment_excluded():
    out = run(entry("theirs", "Investments"))
    assert out.status == "excluded"
    assert out.reason == "other-owner-investment"
    assert out.decided_by == "core:other-owner-investment"
    assert out.category == "Investments"


def test_budget_owner_investment_kept():
    e = entry("mine", "Investments")
    assert run(e) == e


def test_unresolved_entry_untouched():
    e = entry("theirs", "Investments", status="unresolved")
    assert run(e) == e
