"""exclude inputs: owners table, accounts.cash_from, accounts.owner foreign key

Revision ID: 0002_exclude_inputs
Revises: 0001_phase0
"""

from alembic import op

revision = "0002_exclude_inputs"
down_revision = "0001_phase0"
branch_labels = None
depends_on = None

_UP = [
    """create table owners (
        id text primary key,
        display text not null,
        counterparty_strings text[] not null,
        budget boolean not null
    )""",
    "create unique index owners_one_budget on owners (budget) where budget",
    "alter table accounts add column cash_from date null",
    # Rows already stored keep working: every distinct account owner becomes an owner row, none a budget owner.
    "insert into owners (id, display, counterparty_strings, budget)"
    " select distinct owner, owner, '{}'::text[], false from accounts",
    "alter table accounts add constraint accounts_owner_fkey foreign key (owner) references owners (id)",
]

_DOWN = [
    "alter table accounts drop constraint accounts_owner_fkey",
    "alter table accounts drop column cash_from",
    "drop table owners",
]


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWN:
        op.execute(statement)
