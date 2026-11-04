"""fx rates, attribute rulings, account FX floor step and cash opening, cycle and FX ledger columns

Revision ID: 0004_cycle_fx_inputs
Revises: 0003_classify_inputs
"""

from alembic import op

revision = "0004_cycle_fx_inputs"
down_revision = "0003_classify_inputs"
branch_labels = None
depends_on = None

_RULING_KINDS = "'category', 'exclude', 'white_card', 'split', 'reimbursement', 'debt'"
_FX_KINDS = "'sell', 'official', 'effective', 'midpoint'"

_UP = [
    f"""create table fx_rates (
        id text primary key,
        date date not null,
        rate numeric not null,
        kind text not null check (kind in ({_FX_KINDS})),
        method text not null,
        source_ids text null,
        observed_at timestamptz null,
        note text null
    )""",
    "alter table accounts add column fx_income_floor_step numeric(14,2) not null default 5",
    "alter table accounts alter column fx_income_floor_step drop default",
    "alter table accounts add column cash_opening numeric(14,2) null",
    "alter table rulings drop constraint rulings_kind_check",
    f"alter table rulings add constraint rulings_kind_check check (kind in ({_RULING_KINDS}, 'attribute'))",
    "alter table ledger_entries add column budget_month text null",
    "alter table ledger_entries add column amount_uah numeric(14,2) null",
    "alter table ledger_entries add column rate numeric null",
    "alter table ledger_entries add column rate_kind text null",
    "alter table ledger_entries drop constraint ledger_entries_status_check",
    "alter table ledger_entries add constraint ledger_entries_status_check"
    " check (status in ('kept', 'excluded', 'unresolved', 'outside_cycle'))",
]

_DOWN = [
    "alter table ledger_entries drop constraint ledger_entries_status_check",
    "alter table ledger_entries add constraint ledger_entries_status_check"
    " check (status in ('kept', 'excluded', 'unresolved'))",
    "alter table ledger_entries drop column rate_kind",
    "alter table ledger_entries drop column rate",
    "alter table ledger_entries drop column amount_uah",
    "alter table ledger_entries drop column budget_month",
    "alter table rulings drop constraint rulings_kind_check",
    f"alter table rulings add constraint rulings_kind_check check (kind in ({_RULING_KINDS}))",
    "alter table accounts drop column cash_opening",
    "alter table accounts drop column fx_income_floor_step",
    "drop table fx_rates",
]


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWN:
        op.execute(statement)
