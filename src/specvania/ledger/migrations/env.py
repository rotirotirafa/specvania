"""Use the connection supplied by Ledger.open; never create another engine."""

from alembic import context
from sqlalchemy.engine import Connection


def run_migrations() -> None:
    connection: Connection = context.config.attributes["connection"]
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
