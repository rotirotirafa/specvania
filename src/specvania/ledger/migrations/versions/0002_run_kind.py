"""Distinguish planning runs from world execution runs."""

from alembic import op
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, Column, Text

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade(operations: Operations | None = None) -> None:
    migration_ops = operations if operations is not None else op
    # An inline check works with SQLite ADD COLUMN without rebuilding runs
    # (which has incoming foreign keys), and also works on Postgres.
    migration_ops.add_column(
        "runs",
        Column(
            "kind",
            Text,
            CheckConstraint("kind IN ('run', 'plan')", name="ck_run_kind"),
            nullable=False,
            server_default="run",
        ),
    )


def downgrade(operations: Operations | None = None) -> None:
    migration_ops = operations if operations is not None else op
    migration_ops.drop_column("runs", "kind")
