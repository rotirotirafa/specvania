"""SQLAlchemy Core schema and adapter details shared by the storage port.

Schema objects are instance-owned. Preserve build_initial_schema when adding
later migrations: revision 0001 must always describe the original schema.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Dialect, Engine
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite drops offsets; normalize aware timestamps to UTC on both adapters."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("Ledger timestamps must include a timezone")
        return value.astimezone(UTC)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )


@dataclass(frozen=True)
class Schema:
    metadata: MetaData
    projects: Table
    features: Table
    spec_versions: Table
    runs: Table
    events: Table
    bugs: Table


def build_initial_schema() -> Schema:
    metadata = MetaData()
    projects = Table(
        "projects",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("repo_root", Text, nullable=False),
        Column("origin_url", Text, nullable=False),
        UniqueConstraint("repo_root", "origin_url", name="uq_project_identity"),
    )
    features = Table(
        "features",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
        Column("slug", Text, nullable=False),
        Column("title", Text, nullable=False),
        Column("status", Text, nullable=False),
        UniqueConstraint("project_id", "slug", name="uq_feature_slug"),
    )
    spec_versions = Table(
        "spec_versions",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("feature_id", String(36), ForeignKey("features.id"), nullable=False),
        Column("sha256", String(64), nullable=False),
        Column("spec_md", Text, nullable=False),
        Column("tasks_md", Text, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        UniqueConstraint("feature_id", "sha256", name="uq_spec_content"),
        UniqueConstraint("id", "feature_id", name="uq_spec_feature"),
    )
    runs = Table(
        "runs",
        metadata,
        Column("id", String(200), primary_key=True),
        Column("feature_id", String(36), ForeignKey("features.id"), nullable=False),
        Column("spec_version_id", String(36), nullable=False),
        Column("branch", Text, nullable=False),
        Column("status", Text, nullable=False),
        Column("started_at", UTCDateTime(), nullable=False),
        Column("finished_at", UTCDateTime()),
        Column("last_seq", Integer, nullable=False, server_default="0"),
        Column("calls", Integer, nullable=False, server_default="0"),
        Column("tokens_in", Integer, nullable=False, server_default="0"),
        Column("tokens_out", Integer, nullable=False, server_default="0"),
        Column("rooms_cleared", Integer, nullable=False, server_default="0"),
        ForeignKeyConstraint(
            ["spec_version_id", "feature_id"],
            ["spec_versions.id", "spec_versions.feature_id"],
            name="fk_run_spec_feature",
        ),
        CheckConstraint("last_seq >= 0", name="ck_run_sequence"),
        CheckConstraint(
            "status IN ('running', 'paused', 'finished', 'aborted')",
            name="ck_run_status",
        ),
    )
    events = Table(
        "events",
        metadata,
        Column("run_id", String(200), ForeignKey("runs.id"), primary_key=True),
        Column("seq", Integer, primary_key=True),
        Column("type", Text, nullable=False),
        Column("ts", UTCDateTime(), nullable=False),
        Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        CheckConstraint("seq > 0", name="ck_event_sequence"),
    )
    bugs = Table(
        "bugs",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
        Column("feature_id", String(36), ForeignKey("features.id")),
        Column("run_id", String(200), ForeignKey("runs.id")),
        Column("room_id", Text),
        Column("title", Text, nullable=False),
        Column("body", Text, nullable=False),
        Column("source", Text, nullable=False),
        Column("status", Text, nullable=False),
        Column("fixed_by_run", String(200), ForeignKey("runs.id")),
        CheckConstraint("status IN ('open', 'fixed', 'wontfix')", name="ck_bug_status"),
        CheckConstraint(
            "source IN ('user', 'room_skipped', 'boss_scenario')", name="ck_bug_source"
        ),
        CheckConstraint(
            "(status = 'fixed' AND fixed_by_run IS NOT NULL) OR "
            "(status <> 'fixed' AND fixed_by_run IS NULL)",
            name="ck_bug_fixed_run",
        ),
    )
    return Schema(metadata, projects, features, spec_versions, runs, events, bugs)


def build_schema() -> Schema:
    """Current schema; the builder used by revision 0001 remains unchanged."""
    schema = build_initial_schema()
    schema.runs.append_column(
        Column("kind", Text, nullable=False, server_default="run")
    )
    schema.runs.append_constraint(
        CheckConstraint("kind IN ('run', 'plan')", name="ck_run_kind")
    )
    return schema


def configure_sqlite(engine: Engine) -> None:
    """Set connection-local safety and persistent WAL before any migrations."""

    def connect(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    event.listen(engine, "connect", connect)


def lock_migrations(connection: Connection) -> None:
    """Serialize first-open migrations in the ledger, including across processes."""
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        # A stable, ledger-specific transaction advisory lock, released on commit.
        connection.execute(select(func.pg_advisory_xact_lock(0x5350454356414E)))
