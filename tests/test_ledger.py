"""The same storage contract runs against SQLite and optional Postgres."""

import builtins
import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any, Literal

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL

from specvania.clock import FakeClock
from specvania.config import LedgerConfig
from specvania.ledger import Ledger, LedgerError, LedgerNotFoundError
from specvania.ledger.repo import RunIdTakenError
from specvania.ledger.schema import build_initial_schema
from specvania.run.events import (
    Action,
    ModelCall,
    RedactionGuard,
    Role,
    RoomCleared,
    RunFinished,
    RunPaused,
    SecretLeakError,
)
from specvania.run.state import fold_events


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.pg)])
def ledger_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgres":
        url = os.environ.get("SPECVANIA_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Postgres requires SPECVANIA_TEST_DATABASE_URL; run G9")
        return url
    return f"sqlite:///{tmp_path / 'ledger.db'}"


@pytest.fixture
def guard() -> RedactionGuard:
    return RedactionGuard()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ledger(
    ledger_url: str, guard: RedactionGuard, clock: FakeClock
) -> Iterator[Ledger]:
    with Ledger.open(ledger_url, clock=clock, guard=guard) as opened:
        yield opened


def make_run(ledger: Ledger, tmp_path: Path) -> str:
    project = ledger.add_project(tmp_path, "https://example.invalid/repo.git")
    feature = ledger.add_feature(project.id, "invoices", "Invoices")
    version = ledger.add_spec_version(feature.id, "spec", "rooms")
    return ledger.create_run(feature.id, version.id, "specvania/test").id


def action(run_id: str, thought: str = "Read the room") -> Action:
    return Action(
        ts=FakeClock().now(),
        run_id=run_id,
        seq=999,
        room_id="T01",
        thought=thought,
        action="read_file",
        args_summary="README.md",
    )


def test_open_applies_migrations_and_reopen_is_noop(
    ledger_url: str,
    tmp_path: Path,
    guard: RedactionGuard,
) -> None:
    with Ledger.open(ledger_url, clock=FakeClock(), guard=guard) as ledger:
        run_id = make_run(ledger, tmp_path)
        original = action(run_id)
        stored = ledger.append_event(original)
        assert original.seq == 999 and stored.seq == 1
        assert ledger.migration_version == "0002"
    engine = create_engine(ledger_url)
    try:
        assert set(inspect(engine).get_table_names()) == {
            "projects",
            "features",
            "spec_versions",
            "runs",
            "events",
            "bugs",
            "alembic_version",
        }
        with engine.connect() as connection:
            before = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).all()
        with Ledger.open(ledger_url, clock=FakeClock(), guard=guard) as reopened:
            assert reopened.list_events(run_id) == [stored]
            assert reopened.get_run(run_id).last_seq == 1
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).all()
                == before
            )
    finally:
        engine.dispose()


def test_history_records_survive_reopen(
    ledger: Ledger, ledger_url: str, tmp_path: Path, clock: FakeClock
) -> None:
    project = ledger.add_project(tmp_path, "https://example.invalid/origin")
    assert (
        ledger.add_project(tmp_path, "https://example.invalid/origin").id == project.id
    )
    feature = ledger.add_feature(project.id, "invoices", "Invoices", status="planned")
    version = ledger.add_spec_version(feature.id, "spec one", "rooms")
    clock.sleep(1)
    newer = ledger.add_spec_version(feature.id, "spec two", "rooms")
    first = ledger.create_run(feature.id, version.id, "specvania/first")
    clock.sleep(1)
    second = ledger.create_run(feature.id, newer.id, "specvania/second")
    with Ledger.open(ledger_url, clock=FakeClock(), guard=RedactionGuard()) as reopened:
        assert reopened.get_project(project.id) == project
        assert reopened.list_features(project.id) == [feature]
        assert reopened.get_feature(feature.id) == feature
        assert reopened.list_spec_versions(feature.id) == [version, newer]
        assert reopened.get_spec_version(newer.id) == newer
        assert reopened.list_runs(feature.id) == [first, second]


def test_identical_spec_content_returns_existing_version(
    ledger: Ledger,
    tmp_path: Path,
    clock: FakeClock,
) -> None:
    project = ledger.add_project(tmp_path)
    feature = ledger.add_feature(project.id, "one", "One")
    other = ledger.add_feature(project.id, "two", "Two")
    version = ledger.add_spec_version(feature.id, "ab", "c")
    clock.sleep(10)
    assert ledger.add_spec_version(feature.id, "ab", "c") == version
    assert ledger.add_spec_version(feature.id, "a", "bc").id != version.id
    assert ledger.add_spec_version(feature.id, "ab", "changed").id != version.id
    assert ledger.add_spec_version(other.id, "ab", "c").id != version.id
    assert len(version.sha256) == 64
    assert version.spec_md == "ab" and version.tasks_md == "c"
    assert version.created_at == FakeClock().now()
    assert len(ledger.list_spec_versions(feature.id)) == 3


def test_two_threads_append_without_duplicate_or_missing_sequences(
    ledger: Ledger,
    ledger_url: str,
    tmp_path: Path,
) -> None:
    run_id = make_run(ledger, tmp_path)
    barrier = Barrier(2)

    def append_batch() -> list[int]:
        # Separate adapters prove sequencing isn't an instance-owned Python lock.
        with Ledger.open(
            ledger_url, clock=FakeClock(), guard=RedactionGuard()
        ) as writer:
            barrier.wait(timeout=10)
            return [writer.append_event(action(run_id)).seq for _ in range(30)]

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(append_batch) for _ in range(2)]
        sequences = [seq for future in futures for seq in future.result(timeout=30)]
    assert sorted(sequences) == list(range(1, 61))
    assert [event.seq for event in ledger.list_events(run_id)] == list(range(1, 61))
    assert ledger.get_run(run_id).last_seq == 60


@pytest.mark.parametrize("kind", ["run", "plan"])
def test_generated_run_id_retries_suffix_collision(
    ledger: Ledger,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["run", "plan"],
) -> None:
    from specvania.ledger import repo

    first = ledger.get_run(make_run(ledger, tmp_path))
    prefix = first.id[:-4]
    # Shared Postgres retains prior suites: choose a genuinely unused suffix
    # rather than making this regression depend on a fixed id being available.
    for value in range(65536):
        fresh_suffix = f"{value:04x}"
        try:
            ledger.get_run(prefix + fresh_suffix)
        except LedgerNotFoundError:
            break
    else:
        pytest.fail("No unused suffix remains for this test timestamp")
    suffixes = iter([first.id[-4:], fresh_suffix])

    def suffix(size: int) -> str:
        assert size == 2
        return next(suffixes)

    monkeypatch.setattr(repo.secrets, "token_hex", suffix)
    second = ledger.create_run(
        first.feature_id, first.spec_version_id, "", run_id=None, kind=kind
    )
    assert second.id == prefix + fresh_suffix and second.id != first.id
    assert second.kind == kind
    assert second.status == ("finished" if kind == "plan" else "running")
    assert ledger.get_run(first.id) == first
    assert {run.id for run in ledger.list_runs(first.feature_id)} == {
        first.id,
        second.id,
    }


def test_explicit_run_id_collision_does_not_generate_or_retry(
    ledger: Ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from specvania.ledger import repo

    first = ledger.get_run(make_run(ledger, tmp_path))
    add = ledger._add
    attempts: list[str] = []

    def record_attempt(*args: Any, **kwargs: Any) -> Any:
        attempts.append(args[1]["id"])
        return add(*args, **kwargs)

    monkeypatch.setattr(ledger, "_add", record_attempt)
    monkeypatch.setattr(
        repo.secrets,
        "token_hex",
        lambda size: pytest.fail("An explicit run id must not generate another id"),
    )
    with pytest.raises(RunIdTakenError, match="already taken"):
        ledger.create_run(
            first.feature_id, first.spec_version_id, "different", run_id=first.id
        )
    assert attempts == [first.id]
    assert ledger.list_runs(first.feature_id) == [first]


def test_generated_run_id_collision_retries_are_bounded(
    ledger: Ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from specvania.ledger import repo

    first = ledger.get_run(make_run(ledger, tmp_path))
    calls: list[int] = []

    def suffix(size: int) -> str:
        calls.append(size)
        return first.id[-4:]

    monkeypatch.setattr(repo.secrets, "token_hex", suffix)
    with pytest.raises(RunIdTakenError, match="already taken"):
        ledger.create_run(first.feature_id, first.spec_version_id, "different")
    assert calls == [2] * 5
    assert ledger.list_runs(first.feature_id) == [first]


@pytest.mark.parametrize(
    "secret", ["fake-api-key", "fake-view-token", "fake-control-token"]
)
def test_every_append_rejects_secrets_without_consuming_sequence(
    ledger: Ledger,
    guard: RedactionGuard,
    tmp_path: Path,
    secret: str,
) -> None:
    run_id = make_run(ledger, tmp_path)
    assert ledger.append_event(action(run_id)).seq == 1
    guard.register(secret)
    with pytest.raises(SecretLeakError, match="registered secret") as error:
        ledger.append_event(action(run_id, secret))
    assert secret not in str(error.value)
    assert ledger.get_run(run_id).last_seq == 1
    assert ledger.append_event(action(run_id)).seq == 2
    assert len(ledger.list_events(run_id)) == 2


def test_assigned_event_is_guarded_and_rolls_back_counter(
    ledger: Ledger,
    guard: RedactionGuard,
    tmp_path: Path,
) -> None:
    run_id = make_run(ledger, tmp_path)
    guard.register('"seq":1,')
    with pytest.raises(SecretLeakError):
        ledger.append_event(action(run_id))
    assert ledger.list_events(run_id) == []
    assert ledger.get_run(run_id).last_seq == 0


def test_run_totals_status_and_timestamps_project_events(
    ledger: Ledger,
    tmp_path: Path,
) -> None:
    run_id = make_run(ledger, tmp_path)
    clock = FakeClock()
    ledger.append_event(
        ModelCall(
            ts=clock.now(),
            run_id=run_id,
            seq=0,
            role=Role.WARRIOR,
            model="fake",
            tokens_in=7,
            tokens_out=3,
            latency=1.5,
            status=200,
        )
    )
    ledger.append_event(
        RoomCleared(
            ts=clock.now(),
            run_id=run_id,
            seq=0,
            room_id="T01",
            commit="abc",
        )
    )
    ledger.append_event(RunPaused(ts=clock.now(), run_id=run_id, seq=0, reason="lives"))
    assert ledger.get_run(run_id).status == "paused"
    clock.sleep(5)
    ledger.append_event(
        RunFinished(ts=clock.now(), run_id=run_id, seq=0, status="finished")
    )
    run = ledger.get_run(run_id)
    assert (run.calls, run.tokens_in, run.tokens_out, run.rooms_cleared) == (1, 7, 3, 1)
    assert run.started_at == FakeClock().now()
    assert run.finished_at == clock.now()
    assert run.status == fold_events(ledger.list_events(run_id)).status == "finished"


def test_bug_records_and_status_filters(ledger: Ledger, tmp_path: Path) -> None:
    run_id = make_run(ledger, tmp_path)
    run = ledger.get_run(run_id)
    feature = ledger.get_feature(run.feature_id)
    bug = ledger.add_bug(
        feature.project_id,
        "A failed room",
        body="Paladin feedback",
        source="room_skipped",
        feature_id=feature.id,
        run_id=run_id,
        room_id="T01",
    )
    assert ledger.get_bug(bug.id) == bug
    assert ledger.list_bugs(feature.project_id) == [bug]
    fixed = ledger.close_bug(bug.id, status="fixed", fixed_by_run=run_id)
    assert fixed.fixed_by_run == run_id and fixed.status == "fixed"
    assert ledger.list_bugs(feature.project_id) == []
    assert ledger.list_bugs(feature.project_id, include_closed=True) == [fixed]
    user_bug = ledger.add_bug(feature.project_id, "User bug")
    assert ledger.close_bug(user_bug.id, status="wontfix").fixed_by_run is None
    with pytest.raises(LedgerError, match="fixed_by_run"):
        ledger.close_bug(user_bug.id, status="fixed")


def test_foreign_keys_and_missing_records_are_typed_errors(
    ledger: Ledger,
    tmp_path: Path,
) -> None:
    with pytest.raises(LedgerNotFoundError):
        ledger.append_event(action("missing"))
    with pytest.raises(LedgerNotFoundError):
        ledger.get_run("missing")
    with pytest.raises(LedgerError):
        ledger.add_feature("missing", "one", "One")
    run_id = make_run(ledger, tmp_path)
    run = ledger.get_run(run_id)
    feature = ledger.get_feature(run.feature_id)
    other = ledger.add_feature(feature.project_id, "other", "Other")
    with pytest.raises(LedgerError):
        ledger.create_run(other.id, run.spec_version_id, "wrong")


def test_sqlite_uses_wal_and_foreign_keys(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'ledger.db'}"
    with Ledger.open(url, clock=FakeClock(), guard=RedactionGuard()) as ledger:
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                assert (
                    connection.execute(text("PRAGMA journal_mode")).scalar_one()
                    == "wal"
                )
            with pytest.raises(LedgerError):
                ledger.add_feature("missing", "one", "One")
        finally:
            engine.dispose()


@pytest.mark.pg
@pytest.mark.parametrize("ledger_url", ["postgres"], indirect=True)
def test_postgres_event_payload_uses_jsonb(
    ledger_url: str, ledger: Ledger, tmp_path: Path
) -> None:
    run_id = make_run(ledger, tmp_path)
    stored = ledger.append_event(action(run_id))
    engine = create_engine(ledger_url)
    try:
        columns = {
            column["name"]: column for column in inspect(engine).get_columns("events")
        }
        assert isinstance(columns["payload"]["type"], JSONB)
        assert ledger.list_events(run_id) == [stored]
    finally:
        engine.dispose()


def test_config_selects_env_url_or_project_relative_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SPECVANIA_DATABASE_URL", raising=False)
    with Ledger.from_config(
        tmp_path, LedgerConfig(), clock=FakeClock(), guard=RedactionGuard()
    ):
        assert (tmp_path / ".specvania/ledger.db").is_file()
    alternate = tmp_path / "alternate.db"
    monkeypatch.setenv("TEAM_LEDGER", f"sqlite:///{alternate}")
    with Ledger.from_config(
        tmp_path,
        LedgerConfig(database_url_env="TEAM_LEDGER"),
        clock=FakeClock(),
        guard=RedactionGuard(),
    ):
        assert alternate.is_file()


def test_sqlite_without_psycopg_and_clear_postgres_extra_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def without_psycopg(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".")[0] == "psycopg":
            raise ModuleNotFoundError("psycopg is not installed", name="psycopg")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_psycopg)
    with Ledger.open(
        f"sqlite:///{tmp_path / 'ledger.db'}", clock=FakeClock(), guard=RedactionGuard()
    ):
        pass
    with pytest.raises(LedgerError, match=r"specvania\[postgres\]") as error:
        Ledger.open(
            "postgresql://user:fake-password@localhost/specvania",
            clock=FakeClock(),
            guard=RedactionGuard(),
        )
    assert "fake-password" not in str(error.value)


def test_public_ledger_representation_does_not_expose_password() -> None:
    password = "fake-ledger-password-never-use"
    engine = create_engine("sqlite:///:memory:")
    # Credential exposure depends on the engine's URL metadata, not a connection.
    # Keep a real SQLite engine so this regression needs neither psycopg nor sockets.
    engine.url = URL.create(
        "postgresql+psycopg",
        username="user",
        password=password,
        host="example.invalid",
        database="specvania",
    )
    with Ledger(
        engine, build_initial_schema(), FakeClock(), RedactionGuard()
    ) as ledger:
        public_urls = [
            getattr(ledger, name)
            for name in dir(ledger)
            if not name.startswith("_")
            and any(term in name.lower() for term in ("url", "dsn", "connection"))
        ]
        for value in [ledger, *public_urls]:
            assert password not in str(value)
            assert password not in repr(value)


def test_packaged_migrations_work_outside_repository(tmp_path: Path) -> None:
    # A fresh interpreter outside the repo cannot accidentally use root alembic.ini.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from specvania.ledger import Ledger
from specvania.clock import FakeClock
from specvania.run.events import RedactionGuard
with Ledger.open(
    'sqlite:///portable.db', clock=FakeClock(), guard=RedactionGuard()
) as ledger:
    assert ledger.migration_version == '0002'
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "portable.db").is_file()


def test_dependencies_keep_postgres_optional() -> None:
    document = tomllib.loads(Path("pyproject.toml").read_text())
    assert "sqlalchemy>=2" in document["project"]["dependencies"]
    assert "alembic" in document["project"]["dependencies"]
    assert document["project"]["optional-dependencies"]["postgres"] == [
        "psycopg[binary]>=3"
    ]
    assert not any("psycopg" in name for name in document["project"]["dependencies"])


def test_ci_starts_compose_postgres_and_runs_ledger_contract() -> None:
    workflow = json.loads(Path(".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["postgres"]
    commands = [step.get("run", "") for step in job["steps"]]
    start = next(
        index
        for index, command in enumerate(commands)
        if "docker compose up" in command and "postgres" in command
    )
    suite = next(
        index
        for index, command in enumerate(commands)
        if "pytest" in command and "tests/test_ledger.py" in command
    )
    assert start < suite
    assert job["env"]["SPECVANIA_TEST_DATABASE_URL"].startswith("postgresql+psycopg://")
    assert any("--extra postgres" in command for command in commands)


@pytest.mark.parametrize("room", [{}, {"room_id": "T01"}])
def test_model_requested_ledger_payload_roundtrip_without_migration(
    ledger: Ledger, ledger_url: str, tmp_path: Path, room: dict[str, object]
) -> None:
    from specvania.run.events import EVENT_ADAPTER
    from specvania.run.jsonl import decode_events, encode_events

    run_id = make_run(ledger, tmp_path)
    event = EVENT_ADAPTER.validate_python(
        {
            "type": "model_requested",
            "ts": "2026-10-14T12:00:00Z",
            "run_id": run_id,
            "seq": 0,
            "role": "mage",
            "model": "fake/model",
            **room,
        }
    )
    version = ledger.migration_version
    stored = ledger.append_event(event)
    assert stored.seq == 1
    assert ledger.migration_version == version == "0002"
    with Ledger.open(ledger_url, clock=FakeClock(), guard=RedactionGuard()) as reopened:
        assert reopened.list_events(run_id) == [stored]
        assert decode_events(
            encode_events(reopened.list_events(run_id), RedactionGuard()),
            RedactionGuard(),
        ) == (stored,)
        run = reopened.get_run(run_id)
        assert run.calls == run.tokens_in == run.tokens_out == 0
