"""plan_lines: the plan grid imported from the old repo's Notion plan database

Revision ID: 0006_plan_lines
Revises: 0005_month_summaries
"""

from alembic import op

revision = "0006_plan_lines"
down_revision = "0005_month_summaries"
branch_labels = None
depends_on = None

_UP = [
    """create table plan_lines (
        id text primary key,
        budget_month text not null,
        category text not null,
        subcategory text not null default '',
        currency text not null,
        amount numeric(14,2) not null,
        source text,
        handed_over boolean not null default false
    )""",
    "create unique index plan_lines_key on plan_lines (budget_month, category, subcategory, currency)",
]


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    op.execute("drop table plan_lines")
