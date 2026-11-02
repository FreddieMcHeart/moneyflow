"""phase 0 schema

Revision ID: 0001_phase0
Revises:
"""

from alembic import op

revision = "0001_phase0"
down_revision = None
branch_labels = None
depends_on = None

_CREATED_BY = "check (created_by in ('import', 'operator', 'agent'))"

_UP = [
    """create table accounts (
        id text primary key,
        name text not null,
        currency text not null,
        owner text not null,
        role text not null,
        include boolean not null
    )""",
    """create table transactions (
        id text primary key,
        account_id text not null references accounts (id),
        date date not null,
        time timestamptz not null,
        amount numeric(14,2) not null check (amount >= 0),
        currency text not null,
        direction text not null check (direction in ('income', 'expense')),
        mcc integer null,
        description text not null,
        comment text not null
    )""",
    f"""create table rules (
        id text primary key,
        kind text not null,
        priority integer not null,
        ordinal integer not null,
        match jsonb not null,
        result jsonb not null,
        active boolean not null,
        created_by text not null {_CREATED_BY},
        note text null
    )""",
    f"""create table rulings (
        id text primary key,
        transaction_id text not null references transactions (id),
        kind text not null,
        payload jsonb not null,
        created_by text not null {_CREATED_BY}
    )""",
    f"""create table categories (
        name text primary key,
        created_by text not null {_CREATED_BY}
    )""",
    """create table ledger_entries (
        transaction_id text not null,
        part integer not null,
        date date not null,
        account_id text not null,
        category text null,
        subcategory text null,
        amount numeric(14,2) not null,
        currency text not null,
        status text not null check (status in ('kept', 'excluded', 'unresolved')),
        reason text null,
        decided_by text null,
        primary key (transaction_id, part)
    )""",
]

_DOWN = ["ledger_entries", "categories", "rulings", "rules", "transactions", "accounts"]


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    for table in _DOWN:
        op.execute(f"drop table {table}")
