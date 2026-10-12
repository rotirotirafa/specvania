"""Migration and storage contract for planning runs on both ledger adapters."""

import os
from collections.abc import Iterator
from importlib.resources import as_file, files
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, insert, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from specvania.clock import FakeClock
from specvania.ledger import Ledger
from specvania.ledger.schema import build_initial_schema
from specvania.run.events import RedactionGuard


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.pg)])
def ledger_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'ledger.db'}"
        return
    url = os.environ.get("SPECVANIA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Postgres requires SPECVANIA_TEST_DATABASE_URL; reviewer runs G9")
    # Migration tests need an isolated revision-0001 schema, not the shared head.
    engine = create_engine(url)
    schema = "plan_" + uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield (
            make_url(url)
            .update_query_dict({"options": f"-csearch_path={schema}"})
            .render_as_string(hide_password=False)
        )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_plan_run_has_kind_and_finished_status(ledger_url: str, tmp_path: Path) -> None:
    clock = FakeClock()
    with Ledger.open(ledger_url, clock=clock, guard=RedactionGuard()) as ledger:
        project = ledger.add_project(tmp_path)
        feature = ledger.add_feature(project.id, "invoices", "Invoices")
        version = ledger.add_spec_version(feature.id, "spec", "rooms")
        run = ledger.create_run(feature.id, version.id, "", kind="plan")
        assert run.kind == "plan"
        assert run.status == "finished"
        assert run.branch == ""
        assert run.finished_at == clock.now()
        assert ledger.get_run(run.id) == run
        execution = ledger.create_run(feature.id, version.id, "specvania/execution")
        assert execution.kind == "run" and execution.status == "running"
        assert execution.finished_at is None
    with Ledger.open(ledger_url, clock=clock, guard=RedactionGuard()) as reopened:
        assert reopened.get_run(run.id).kind == "plan"
        assert reopened.get_run(execution.id).kind == "run"


def test_open_upgrades_0001_preserving_existing_runs(ledger_url: str) -> None:
    engine = create_engine(ledger_url)
    clock = FakeClock()
    initial = build_initial_schema()
    try:
        with engine.begin() as connection:
            with as_file(files("specvania.ledger.migrations")) as directory:
                scripts = ScriptDirectory(str(directory))
                revision = scripts.get_revision("0001")
                assert revision is not None
                context = MigrationContext.configure(connection)
                revision.module.upgrade(operations=Operations(context))
                context.stamp(scripts, "0001")
            connection.execute(
                insert(initial.projects).values(
                    id="project", repo_root="/test", origin_url=""
                )
            )
            connection.execute(
                insert(initial.features).values(
                    id="feature",
                    project_id="project",
                    slug="invoices",
                    title="Invoices",
                    status="planned",
                )
            )
            connection.execute(
                insert(initial.spec_versions).values(
                    id="version",
                    feature_id="feature",
                    sha256="a" * 64,
                    spec_md="spec",
                    tasks_md="rooms",
                    created_at=clock.now(),
                )
            )
            connection.execute(
                insert(initial.runs).values(
                    id="existing",
                    feature_id="feature",
                    spec_version_id="version",
                    branch="specvania/existing",
                    status="running",
                    started_at=clock.now(),
                )
            )
        assert "kind" not in {
            column["name"] for column in inspect(engine).get_columns("runs")
        }
        with Ledger.open(ledger_url, clock=clock, guard=RedactionGuard()) as ledger:
            assert ledger.migration_version == "0002"
            existing = ledger.get_run("existing")
            assert existing.kind == "run"
            assert existing.branch == "specvania/existing"
            assert existing.spec_version_id == "version"
            assert existing.status == "running" and existing.last_seq == 0
            assert existing.started_at == clock.now()
        with Ledger.open(ledger_url, clock=clock, guard=RedactionGuard()) as reopened:
            assert reopened.migration_version == "0002"
            assert reopened.get_run("existing") == existing
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT kind FROM runs WHERE id = 'existing'")
                ).scalar_one()
                == "run"
            )
    finally:
        engine.dispose()


def test_run_kind_has_server_default_not_null_and_check(
    ledger_url: str, tmp_path: Path
) -> None:
    with Ledger.open(ledger_url, clock=FakeClock(), guard=RedactionGuard()) as ledger:
        project = ledger.add_project(tmp_path)
        feature = ledger.add_feature(project.id, "invoices", "Invoices")
        version = ledger.add_spec_version(feature.id, "spec", "rooms")
        run = ledger.create_run(feature.id, version.id, "specvania/test")
    engine = create_engine(ledger_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO runs (id, feature_id, spec_version_id, branch, "
                    "status, started_at) SELECT 'default-kind', feature_id, "
                    "spec_version_id, branch, status, started_at "
                    "FROM runs WHERE id = :id"
                ),
                {"id": run.id},
            )
            assert (
                connection.execute(
                    text("SELECT kind FROM runs WHERE id = 'default-kind'")
                ).scalar_one()
                == "run"
            )
        for value in (None, "unknown"):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text("UPDATE runs SET kind = :kind WHERE id = :id"),
                        {"id": run.id, "kind": value},
                    )
    finally:
        engine.dispose()
