"""classify inputs: people, transactions.source, kind CHECKs, ledger direction and via

Revision ID: 0003_classify_inputs
Revises: 0002_exclude_inputs
"""

from alembic import op

revision = "0003_classify_inputs"
down_revision = "0002_exclude_inputs"
branch_labels = None
depends_on = None

_RULE_KINDS = "'categorize', 'exclude', 'credit_repayment', 'cancellation', 'person_return', 'investment_withdrawal'"
_RULING_KINDS = "'category', 'exclude', 'white_card', 'split', 'reimbursement', 'debt'"

_UP = [
    """create table people (
        id text primary key,
        display text not null,
        opening_balance numeric(14,2) not null,
        note text null
    )""",
    "alter table transactions add column source text not null default 'bank'"
    " check (source in ('bank', 'manual'))",
    "alter table transactions alter column source drop default",
    f"alter table rules add constraint rules_kind_check check (kind in ({_RULE_KINDS}))",
    f"alter table rulings add constraint rulings_kind_check check (kind in ({_RULING_KINDS}))",
    # A stored ledger predates the column: its direction is the sign it was saved with.
    "alter table ledger_entries add column direction text null"
    " check (direction in ('income', 'expense', 'reimbursement'))",
    "update ledger_entries set direction = case when amount < 0 then 'expense' else 'income' end",
    "alter table ledger_entries alter column direction set not null",
    "alter table ledger_entries add column via text null",
]

_DOWN = [
    "alter table ledger_entries drop column via",
    "alter table ledger_entries drop column direction",
    "alter table rulings drop constraint rulings_kind_check",
    "alter table rules drop constraint rules_kind_check",
    "alter table transactions drop column source",
    "drop table people",
]


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWN:
        op.execute(statement)
