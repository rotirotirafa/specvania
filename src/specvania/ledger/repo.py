"""Synchronous storage port; domain callers never need SQLAlchemy objects."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self
from uuid import uuid4

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import Table, create_engine, insert, select, update
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.sql.elements import ColumnElement

from specvania.clock import Clock
from specvania.config import LedgerConfig
from specvania.ledger.schema import (
    Schema,
    build_schema,
    configure_sqlite,
    lock_migrations,
)
from specvania.run.events import EVENT_ADAPTER, Event, RedactionGuard


class LedgerError(Exception):
    """A storage/configuration problem the player can fix; never includes a URL."""


class LedgerNotFoundError(LedgerError):
    """A referenced ledger record does not exist."""


class RunIdTakenError(LedgerError):
    """A run id collided; callers can retry without repeating model calls."""


def generate_run_id(clock: Clock) -> str:
    """Keep the domain format; uniqueness is enforced by the Ledger."""
    return f"{clock.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str


class Project(Record):
    repo_root: str
    origin_url: str


class Feature(Record):
    project_id: str
    slug: str
    title: str
    status: str


class SpecVersion(Record):
    feature_id: str
    sha256: str
    spec_md: str
    tasks_md: str
    created_at: AwareDatetime


class Run(Record):
    kind: Literal["run", "plan"]
    feature_id: str
    spec_version_id: str
    branch: str
    status: Literal["running", "paused", "finished", "aborted"]
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    last_seq: int
    calls: int
    tokens_in: int
    tokens_out: int
    rooms_cleared: int


class Bug(Record):
    project_id: str
    feature_id: str | None
    run_id: str | None
    room_id: str | None
    title: str
    body: str
    source: Literal["user", "room_skipped", "boss_scenario"]
    status: Literal["open", "fixed", "wontfix"]
    fixed_by_run: str | None


class Ledger:
    def __init__(
        self, engine: Engine, schema: Schema, clock: Clock, guard: RedactionGuard
    ) -> None:
        self._engine = engine
        self._schema = schema
        self._clock = clock
        self._guard = guard

    @classmethod
    def open(cls, url: str | URL, *, clock: Clock, guard: RedactionGuard) -> Self:
        engine: Engine | None = None
        try:
            parsed = make_url(url)
            if parsed.get_backend_name() == "postgresql":
                if parsed.drivername not in ("postgresql", "postgresql+psycopg"):
                    raise LedgerError("Postgres requires the psycopg 3 adapter.")
                parsed = parsed.set(drivername="postgresql+psycopg")
            elif parsed.drivername != "sqlite":
                raise LedgerError("Configure a SQLite or Postgres ledger URL.")
            if parsed.get_backend_name() == "sqlite" and parsed.database not in (
                None,
                "",
                ":memory:",
            ):
                Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(parsed)
            if engine.dialect.name == "sqlite":
                configure_sqlite(engine)
            ledger = cls(engine, build_schema(), clock, guard)
            ledger._migrate()
            return ledger
        except (ImportError, ModuleNotFoundError):
            if engine is not None:
                engine.dispose()
            raise LedgerError(
                "Postgres support requires pip install 'specvania[postgres]'."
            ) from None
        except (SQLAlchemyError, OSError, ValueError, CommandError):
            if engine is not None:
                engine.dispose()
            raise LedgerError(
                "Cannot open ledger; check its URL, connection and permissions."
            ) from None

    @classmethod
    def from_config(
        cls,
        root: Path,
        config: LedgerConfig,
        *,
        clock: Clock,
        guard: RedactionGuard,
        environ: Mapping[str, str] | None = None,
    ) -> Self:
        environment = os.environ if environ is None else environ
        configured = environment.get(config.database_url_env)
        url = configured or URL.create(
            "sqlite", database=str((root / config.sqlite_path).resolve())
        )
        return cls.open(url, clock=clock, guard=guard)

    def _migrate(self) -> None:
        # Bind operations explicitly instead of Alembic's process-global op
        # proxy, so different adapters can open concurrently without shared state.
        with as_file(files("specvania.ledger.migrations")) as directory:
            scripts = ScriptDirectory(str(directory))
            with self._engine.begin() as connection:
                lock_migrations(connection)
                context = MigrationContext.configure(connection)
                current = context.get_current_revision()
                revisions = list(scripts.iterate_revisions("head", current))
                for revision in reversed(revisions):
                    revision.module.upgrade(operations=Operations(context))
                    context.stamp(scripts, revision.revision)

    @property
    def migration_version(self) -> str | None:
        with self._transaction() as connection:
            return MigrationContext.configure(connection).get_current_revision()

    @contextmanager
    def _transaction(self) -> Iterator[Connection]:
        try:
            with self._engine.begin() as connection:
                yield connection
        except SQLAlchemyError:
            raise LedgerError(
                "Cannot access ledger; check references, connection and permissions."
            ) from None

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _get[RecordT: BaseModel](
        self, table: Table, identifier: str, model: type[RecordT]
    ) -> RecordT:
        with self._transaction() as connection:
            row = (
                connection.execute(select(table).where(table.c.id == identifier))
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LedgerNotFoundError(f"No {table.name} record with that id.")
            return model.model_validate(dict(row))

    def _list[RecordT: BaseModel](
        self,
        table: Table,
        condition: ColumnElement[bool],
        model: type[RecordT],
    ) -> list[RecordT]:
        order = (
            [table.c.created_at, table.c.id]
            if "created_at" in table.c
            else [table.c.id]
        )
        if "started_at" in table.c:
            order = [table.c.started_at, table.c.id]
        with self._transaction() as connection:
            rows = connection.execute(
                select(table).where(condition).order_by(*order)
            ).mappings()
            return [model.model_validate(dict(row)) for row in rows]

    def _add[RecordT: BaseModel](
        self,
        table: Table,
        values: dict[str, Any],
        model: type[RecordT],
        identity: ColumnElement[bool] | None = None,
        identity_error: type[LedgerError] | None = None,
    ) -> RecordT:
        # A unique constraint handles concurrent deduplication on both adapters.
        # Catch outside the failed transaction, then read in a fresh transaction.
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(table).values(**values))
        except IntegrityError:
            if identity is not None:
                matches = self._list(table, identity, model)
                if matches:
                    if identity_error is not None:
                        raise identity_error("Run id is already taken.") from None
                    return matches[0]
            raise LedgerError(
                "Cannot add ledger record; check references and unique identities."
            ) from None
        except SQLAlchemyError:
            raise LedgerError(
                "Cannot add ledger record; check connection and permissions."
            ) from None
        return self._get(table, values["id"], model)

    def add_project(self, repo_root: Path, origin_url: str = "") -> Project:
        table = self._schema.projects
        root = str(repo_root.resolve())
        return self._add(
            table,
            {"id": str(uuid4()), "repo_root": root, "origin_url": origin_url},
            Project,
            (table.c.repo_root == root) & (table.c.origin_url == origin_url),
        )

    def get_project(self, project_id: str) -> Project:
        return self._get(self._schema.projects, project_id, Project)

    def add_feature(
        self, project_id: str, slug: str, title: str, *, status: str = "planned"
    ) -> Feature:
        table = self._schema.features
        return self._add(
            table,
            {
                "id": str(uuid4()),
                "project_id": project_id,
                "slug": slug,
                "title": title,
                "status": status,
            },
            Feature,
            (table.c.project_id == project_id) & (table.c.slug == slug),
        )

    def get_feature(self, feature_id: str) -> Feature:
        return self._get(self._schema.features, feature_id, Feature)

    def list_features(self, project_id: str) -> list[Feature]:
        return self._list(
            self._schema.features,
            self._schema.features.c.project_id == project_id,
            Feature,
        )

    def add_spec_version(
        self, feature_id: str, spec_md: str, tasks_md: str
    ) -> SpecVersion:
        # Length-delimited JSON avoids ambiguity between ('ab', 'c') and ('a', 'bc').
        content = json.dumps(
            [spec_md, tasks_md], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        table = self._schema.spec_versions
        return self._add(
            table,
            {
                "id": str(uuid4()),
                "feature_id": feature_id,
                "sha256": digest,
                "spec_md": spec_md,
                "tasks_md": tasks_md,
                "created_at": self._clock.now(),
            },
            SpecVersion,
            (table.c.feature_id == feature_id) & (table.c.sha256 == digest),
        )

    def get_spec_version(self, version_id: str) -> SpecVersion:
        return self._get(self._schema.spec_versions, version_id, SpecVersion)

    def list_spec_versions(self, feature_id: str) -> list[SpecVersion]:
        table = self._schema.spec_versions
        return self._list(table, table.c.feature_id == feature_id, SpecVersion)

    def create_run(
        self,
        feature_id: str,
        spec_version_id: str,
        branch: str,
        *,
        run_id: str | None = None,
        kind: Literal["run", "plan"] = "run",
    ) -> Run:
        """Plan rows represent validated plans, with an empty branch by convention.

        Planning buffers events before creating this finished row; unsuccessful
        plans have no ledger records. Execution rows still start as running.
        Generated ids get up to five attempts; explicit ids fail immediately
        on collision so callers can preserve their branch and event identities.
        """
        now = self._clock.now()
        attempts = 5 if run_id is None else 1
        for attempt in range(attempts):
            identifier = generate_run_id(self._clock) if run_id is None else run_id
            try:
                return self._add(
                    self._schema.runs,
                    {
                        "id": identifier,
                        "feature_id": feature_id,
                        "spec_version_id": spec_version_id,
                        "branch": branch,
                        "kind": kind,
                        "status": "finished" if kind == "plan" else "running",
                        "started_at": now,
                        "finished_at": now if kind == "plan" else None,
                    },
                    Run,
                    self._schema.runs.c.id == identifier,
                    identity_error=RunIdTakenError,
                )
            except RunIdTakenError:
                if attempt == attempts - 1:
                    raise
        raise AssertionError("Unreachable run id allocation state")

    def get_run(self, run_id: str) -> Run:
        return self._get(self._schema.runs, run_id, Run)

    def list_runs(self, feature_id: str) -> list[Run]:
        table = self._schema.runs
        return self._list(table, table.c.feature_id == feature_id, Run)

    def append_event(self, event: Event) -> Event:
        self._guard.validate(event)
        runs = self._schema.runs
        with self._transaction() as connection:
            # UPDATE locks the run row on Postgres and the writer on SQLite.
            # Allocation, payload insert and projections commit or roll back together.
            seq = connection.execute(
                update(runs)
                .where(runs.c.id == event.run_id)
                .values(last_seq=runs.c.last_seq + 1)
                .returning(runs.c.last_seq)
            ).scalar_one_or_none()
            if seq is None:
                raise LedgerNotFoundError("No run record with that id.")
            assigned = EVENT_ADAPTER.validate_python(event.model_dump() | {"seq": seq})
            self._guard.validate(assigned)
            connection.execute(
                insert(self._schema.events).values(
                    run_id=assigned.run_id,
                    seq=assigned.seq,
                    type=assigned.type,
                    ts=assigned.ts,
                    payload=assigned.model_dump(mode="json"),
                )
            )
            values: dict[str, Any] = {}
            match assigned.type:
                case "run_started":
                    values = {"status": "running", "branch": assigned.branch}
                case "room_entered":
                    values = {"status": "running"}
                case "model_call":
                    values = {
                        "calls": runs.c.calls + 1,
                        "tokens_in": runs.c.tokens_in + assigned.tokens_in,
                        "tokens_out": runs.c.tokens_out + assigned.tokens_out,
                    }
                case "room_cleared":
                    values = {"rooms_cleared": runs.c.rooms_cleared + 1}
                case "run_paused":
                    values = {"status": "paused"}
                case "run_finished":
                    values = {"status": assigned.status, "finished_at": assigned.ts}
            if values:
                connection.execute(
                    update(runs).where(runs.c.id == assigned.run_id).values(**values)
                )
        return assigned

    def list_events(self, run_id: str) -> list[Event]:
        self.get_run(run_id)
        table = self._schema.events
        with self._transaction() as connection:
            payloads = connection.execute(
                select(table.c.payload)
                .where(table.c.run_id == run_id)
                .order_by(table.c.seq)
            ).scalars()
            return [EVENT_ADAPTER.validate_python(payload) for payload in payloads]

    def add_bug(
        self,
        project_id: str,
        title: str,
        *,
        body: str = "",
        source: Literal["user", "room_skipped", "boss_scenario"] = "user",
        feature_id: str | None = None,
        run_id: str | None = None,
        room_id: str | None = None,
    ) -> Bug:
        return self._add(
            self._schema.bugs,
            {
                "id": str(uuid4()),
                "project_id": project_id,
                "feature_id": feature_id,
                "run_id": run_id,
                "room_id": room_id,
                "title": title,
                "body": body,
                "source": source,
                "status": "open",
                "fixed_by_run": None,
            },
            Bug,
        )

    def get_bug(self, bug_id: str) -> Bug:
        return self._get(self._schema.bugs, bug_id, Bug)

    def list_bugs(self, project_id: str, *, include_closed: bool = False) -> list[Bug]:
        table = self._schema.bugs
        condition = table.c.project_id == project_id
        if not include_closed:
            condition &= table.c.status == "open"
        return self._list(table, condition, Bug)

    def close_bug(
        self,
        bug_id: str,
        *,
        status: Literal["fixed", "wontfix"],
        fixed_by_run: str | None = None,
    ) -> Bug:
        if (status == "fixed") != (fixed_by_run is not None):
            raise LedgerError(
                "A fixed bug requires fixed_by_run; wontfix must not set it."
            )
        self.get_bug(bug_id)
        with self._transaction() as connection:
            connection.execute(
                update(self._schema.bugs)
                .where(self._schema.bugs.c.id == bug_id)
                .values(status=status, fixed_by_run=fixed_by_run)
            )
        return self.get_bug(bug_id)
