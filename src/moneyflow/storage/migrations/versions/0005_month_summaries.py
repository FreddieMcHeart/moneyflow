"""month_summaries: the derived per-cycle, per-currency summary, rebuilt with the ledger

Revision ID: 0005_month_summaries
Revises: 0004_cycle_fx_inputs
"""

from alembic import op

revision = "0005_month_summaries"
down_revision = "0004_cycle_fx_inputs"
branch_labels = None
depends_on = None

_UP = [
    """create table month_summaries (
        budget_month text not null,
        currency text not null,
        income numeric(14,2) not null,
        total_actual numeric(14,2) not null,
        cycle_start date not null,
        cycle_end date null,
        cycle_open boolean not null,
        days_total integer null,
        days_left integer null,
        cash_in_hand numeric(14,2) null,
        built_for date not null,
        primary key (budget_month, currency)
    )""",
]


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    op.execute("drop table month_summaries")
