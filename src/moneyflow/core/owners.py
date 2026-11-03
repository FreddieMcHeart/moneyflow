"""Owner helpers shared by the exclude mechanisms (A6, A9, A12) and the late step (A17)."""

from moneyflow.core.model import BuildRefused, Inputs, Owner


def owner_of(account_id: str, inputs: Inputs) -> str:
    for account in inputs.accounts:
        if account.id == account_id:
            return account.owner
    raise BuildRefused(f"unknown account {account_id}", None)


def budget_owner(inputs: Inputs) -> Owner:
    """The one owner whose spending is the budget; the build refuses unless exactly one is flagged."""
    flagged = [o for o in inputs.owners if o.budget]
    if len(flagged) != 1:
        raise BuildRefused(f"exactly one budget owner required, found {len(flagged)}", None)
    return flagged[0]


def is_budget_owner_account(account_id: str, inputs: Inputs) -> bool:
    return owner_of(account_id, inputs) == budget_owner(inputs).id
