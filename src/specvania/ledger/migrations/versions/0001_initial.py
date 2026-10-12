"""Create the original six-table ledger on SQLite or Postgres."""

from alembic import op
from alembic.operations import Operations

from specvania.ledger.schema import build_initial_schema

revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade(operations: Operations | None = None) -> None:
    # The versioned schema builder is frozen at this revision; subsequent schema
    # changes require a new revision rather than edits to build_initial_schema.
    bind = operations.get_bind() if operations is not None else op.get_bind()
    build_initial_schema().metadata.create_all(bind, checkfirst=False)


def downgrade(operations: Operations | None = None) -> None:
    bind = operations.get_bind() if operations is not None else op.get_bind()
    build_initial_schema().metadata.drop_all(bind, checkfirst=False)
