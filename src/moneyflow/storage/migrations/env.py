"""Alembic environment: the URL comes from `sqlalchemy.url` or MONEYFLOW_DATABASE_URL."""

import os

from alembic import context
from sqlalchemy import create_engine


def _url() -> str:
    url = context.config.get_main_option("sqlalchemy.url") or os.environ["MONEYFLOW_DATABASE_URL"]
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
