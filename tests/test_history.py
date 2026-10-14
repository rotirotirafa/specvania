"""T12 history/status/export contracts against SQLite and optional Postgres."""

import os
from pathlib import Path

import pytest
from test_engine import Harness, boss, git, success, write_world
from test_engine import harness as harness
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.ledger.repo import Ledger
from specvania.run.events import (
    EVENT_ADAPTER,
    AttemptStarted,
    ModelCall,
    RedactionGuard,
    Role,
    RoomCleared,
    RunFinished,
    RunStarted,
    SpecWritten,
)
from specvania.run.jsonl import decode_events


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.pg)])
def ledger_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgres":
        url = os.environ.get("SPECVANIA_TEST_DATABASE_URL")
        if not url:
            pytest.skip(
                "Postgres requires SPECVANIA_TEST_DATABASE_URL; reviewer runs G9"
            )
        return url
    return f"sqlite:///{tmp_path / 'ledger.db'}"


@pytest.fixture(params=[False, True], ids=["legacy", "model_requested"])
def saved_history(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    ledger_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, str, str, str, str]:
    # Git is read-only for these commands; use a fresh temporary repository.
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    (root / ".specvania").mkdir()
    (root / ".specvania/config.toml").write_text("")
    monkeypatch.chdir(root)
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", ledger_url)
    clock = FakeClock()
    guard = RedactionGuard()
    guard.register(os.environ["NVIDIA_API_KEY"])
    guard.register(ledger_url)
    with Ledger.open(ledger_url, clock=clock, guard=guard) as ledger:
        project = ledger.add_project(root)
        feature = ledger.add_feature(
            project.id, "invoices", "Invoices", status="planned"
        )
        first = ledger.add_spec_version(feature.id, "spec before", "rooms")
        first_run = ledger.create_run(feature.id, first.id, "specvania/one")
        ledger.append_event(
            RunStarted(
                ts=clock.now(),
                run_id=first_run.id,
                seq=0,
                feature="invoices",
                branch=first_run.branch,
            )
        )
        ledger.append_event(
            AttemptStarted(
                ts=clock.now(), run_id=first_run.id, seq=0, room_id="T01", attempt=1
            )
        )
        if request.param:
            ledger.append_event(
                EVENT_ADAPTER.validate_python(
                    {
                        "type": "model_requested",
                        "ts": clock.now(),
                        "run_id": first_run.id,
                        "seq": 0,
                        "role": "warrior",
                        "model": "w1",
                        "room_id": "T01",
                    }
                )
            )
        ledger.append_event(
            ModelCall(
                ts=clock.now(),
                run_id=first_run.id,
                seq=0,
                model="w1",
                role=Role.WARRIOR,
                tokens_in=11,
                tokens_out=13,
                latency=0.5,
                status=200,
            )
        )
        ledger.append_event(
            RoomCleared(
                ts=clock.now(),
                run_id=first_run.id,
                seq=0,
                room_id="T01",
                commit="abcdef123",
            )
        )
        ledger.append_event(
            RunFinished(ts=clock.now(), run_id=first_run.id, seq=0, status="finished")
        )
        clock.sleep(2)
        second = ledger.add_spec_version(feature.id, "spec after edit", "rooms")
        second_run = ledger.create_run(feature.id, second.id, "specvania/two")
        ledger.append_event(
            RunStarted(
                ts=clock.now(),
                run_id=second_run.id,
                seq=0,
                feature="invoices",
                branch=second_run.branch,
            )
        )
        ledger.add_bug(project.id, "open bug", feature_id=feature.id, room_id="T01")
        closed = ledger.add_bug(project.id, "closed bug", feature_id=feature.id)
        ledger.close_bug(closed.id, status="wontfix")
        return (
            root,
            feature.id,
            first_run.id,
            second_run.id,
            first.sha256,
            second.sha256,
        )


def test_history_two_spec_versions_and_runs_survive_reopen(
    saved_history: tuple[Path, str, str, str, str, str],
    ledger_url: str,
) -> None:
    root, feature_id, run1, run2, hash1, hash2 = saved_history
    with Ledger.open(ledger_url, clock=FakeClock(), guard=RedactionGuard()) as ledger:
        versions = ledger.list_spec_versions(feature_id)
        assert [v.sha256 for v in versions] == [hash1, hash2]
        assert [r.id for r in ledger.list_runs(feature_id)] == [run1, run2]
    result = CliRunner().invoke(cli.create_app(), ["history", "invoices"])
    assert result.exit_code == 0, result.output
    assert hash1 in result.output and hash2 in result.output
    assert "2000-01-01T00:00:00+00:00" in result.output
    assert "2000-01-01T00:00:02+00:00" in result.output
    assert f"{run1} | run | finished | 1 | 24 | {hash1}" in result.output
    assert f"{run2} | run | running | 0 | 0 | {hash2}" in result.output
    assert "open bug" in result.output and "closed bug" not in result.output
    assert ledger_url not in result.output
    assert os.environ["NVIDIA_API_KEY"] not in result.output
    assert not (root / ".specvania/runs").exists()


def test_history_features_reads_open_bugs_from_ledger(
    saved_history: tuple[Path, str, str, str, str, str],
) -> None:
    result = CliRunner().invoke(cli.create_app(), ["history"])
    assert result.exit_code == 0, result.output
    assert "invoices | planned | 2 | 2 | 1" in result.output


def test_status_explicit_and_latest_with_per_model_and_room_metrics(
    saved_history: tuple[Path, str, str, str, str, str],
) -> None:
    _, _, run1, run2, _, _ = saved_history
    explicit = CliRunner().invoke(cli.create_app(), ["status", run1])
    latest = CliRunner().invoke(cli.create_app(), ["status"])
    assert explicit.exit_code == latest.exit_code == 0
    assert run1 in explicit.output and "Status: finished" in explicit.output
    assert "| w1 | 1 | 11 | 13 | 0 | 0 | 0.5 | not priced |" in explicit.output
    assert "| T01 | 1 | cleared | abcdef123 |" in explicit.output
    assert run2 in latest.output and "Status: running" in latest.output
    assert "currency" not in explicit.output and "USD" not in explicit.output


def test_plan_runs_are_labeled_and_excluded_from_rooms_cleared(
    saved_history: tuple[Path, str, str, str, str, str],
    ledger_url: str,
) -> None:
    _, feature_id, _, _, _, hash2 = saved_history
    clock = FakeClock()
    clock.sleep(4)
    with Ledger.open(ledger_url, clock=clock, guard=RedactionGuard()) as ledger:
        version = ledger.list_spec_versions(feature_id)[-1]
        plan = ledger.create_run(feature_id, version.id, "", kind="plan")
        ledger.append_event(
            SpecWritten(
                ts=clock.now(),
                run_id=plan.id,
                seq=0,
                feature="invoices",
                spec_version=version.id,
            )
        )
        ledger.append_event(
            ModelCall(
                ts=clock.now(),
                run_id=plan.id,
                seq=0,
                role=Role.MAGE,
                model="mage1",
                tokens_in=3,
                tokens_out=5,
                latency=1,
                status=200,
            )
        )
        # Defensive: imported plan events must never contribute room counts.
        ledger.append_event(
            RoomCleared(
                ts=clock.now(),
                run_id=plan.id,
                seq=0,
                room_id="T99",
                commit="invalid-plan-room",
            )
        )
    result = CliRunner().invoke(cli.create_app(), ["history", "invoices"])
    assert result.exit_code == 0, result.output
    assert f"{plan.id} | plan | finished | n/a | 8 | {hash2}" in result.output
    status = CliRunner().invoke(cli.create_app(), ["status"])
    assert status.exit_code == 0, status.output
    assert plan.id in status.output and "Kind: plan" in status.output
    assert "Status: finished" in status.output
    assert "Rooms cleared: n/a" in status.output
    assert "T99" not in status.output


def test_export_round_trips_exact_ledger_events(
    saved_history: tuple[Path, str, str, str, str, str],
    ledger_url: str,
) -> None:
    root, _, run1, _, _, _ = saved_history
    with Ledger.open(ledger_url, clock=FakeClock(), guard=RedactionGuard()) as ledger:
        expected = tuple(ledger.list_events(run1))
    result = CliRunner().invoke(cli.create_app(), ["export", run1])
    assert result.exit_code == 0, result.output
    path = root / f".specvania/runs/{run1}/events.jsonl"
    stream = path.read_text()
    assert decode_events(stream, RedactionGuard()) == expected
    assert stream.endswith("\n")
    assert ledger_url not in stream + result.output
    assert os.environ["NVIDIA_API_KEY"] not in stream + result.output


@pytest.mark.parametrize(
    "command",
    [
        ["status", "missing"],
        ["history", "missing"],
        ["export", "missing"],
    ],
)
def test_unknown_record_is_clear_error_without_traceback(
    saved_history: tuple[Path, str, str, str, str, str],
    command: list[str],
) -> None:
    result = CliRunner().invoke(cli.create_app(), command)
    assert result.exit_code == 1, result.output
    assert "not implemented" not in result.output and "Traceback" not in result.output
    assert os.environ["NVIDIA_API_KEY"] not in result.output


@pytest.mark.parametrize("command", [["status"], ["history"]])
def test_empty_project_history_or_latest_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
) -> None:
    git(tmp_path, "init")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPECVANIA_DATABASE_URL", raising=False)
    (tmp_path / ".specvania").mkdir()
    (tmp_path / ".specvania/config.toml").write_text("")
    result = CliRunner().invoke(cli.create_app(), command)
    assert result.exit_code == (0 if command == ["history"] else 1), result.output
    assert "No features" in result.output or "No runs" in result.output


@pytest.mark.parametrize("secret_env", ["NVIDIA_API_KEY", "SPECVANIA_DATABASE_URL"])
def test_history_rejects_secrets_in_non_event_metadata(
    saved_history: tuple[Path, str, str, str, str, str],
    ledger_url: str,
    secret_env: str,
) -> None:
    root, _, _, _, _, _ = saved_history
    secret = os.environ[secret_env]
    with Ledger.open(ledger_url, clock=FakeClock(), guard=RedactionGuard()) as ledger:
        project = ledger.add_project(root)
        ledger.add_feature(project.id, secret, "Secret title")
    result = CliRunner().invoke(cli.create_app(), ["history"])
    assert result.exit_code == 1, result.output
    assert secret not in result.output


@pytest.mark.parametrize(
    "secret_env",
    [
        "NVIDIA_API_KEY",
        "SPECVANIA_VIEW_TOKEN",
        "SPECVANIA_CONTROL_TOKEN",
        "SPECVANIA_DATABASE_URL",
    ],
)
def test_export_rejects_secrets_in_events(
    saved_history: tuple[Path, str, str, str, str, str],
    ledger_url: str,
    monkeypatch: pytest.MonkeyPatch,
    secret_env: str,
) -> None:
    root, _, run1, _, _, _ = saved_history
    # A legacy/foreign ledger may lack the live guard: export checks again.
    secret = (
        ledger_url if secret_env == "SPECVANIA_DATABASE_URL" else "fake-secret-value"
    )
    monkeypatch.setenv(secret_env, secret)
    with Ledger.open(ledger_url, clock=FakeClock(), guard=RedactionGuard()) as ledger:
        ledger.append_event(
            RunStarted(
                ts=FakeClock().now(), run_id=run1, seq=0, feature=secret, branch="safe"
            )
        )
    result = CliRunner().invoke(cli.create_app(), ["export", run1])
    assert result.exit_code == 1, result.output
    assert secret not in result.output
    assert not (root / f".specvania/runs/{run1}/events.jsonl").exists()


def test_history_s13_engine_runs_after_spec_edit_and_reopen(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    first = harness.engine([*success("T01"), boss()]).run(directory)
    assert first.run_id is not None
    first_row = harness.ledger.get_run(first.run_id)
    first_version = harness.ledger.get_spec_version(first_row.spec_version_id)
    path = directory / "spec.md"
    path.write_text(path.read_text() + "\nChanged acceptance context.\n")
    git(harness.root, "add", "world/spec.md")
    git(harness.root, "commit", "-m", "edit spec")
    harness.clock.sleep(2)
    second = harness.engine([*success("T01"), boss()]).run(directory)
    assert second.run_id is not None
    second_row = harness.ledger.get_run(second.run_id)
    second_version = harness.ledger.get_spec_version(second_row.spec_version_id)
    assert first_version.sha256 != second_version.sha256
    harness.ledger.close()
    with Ledger.open(
        harness.ledger_url, clock=harness.clock, guard=RedactionGuard()
    ) as reopened:
        assert len(reopened.list_spec_versions(first_row.feature_id)) == 2
        assert [r.id for r in reopened.list_runs(first_row.feature_id)] == [
            first.run_id,
            second.run_id,
        ]
    monkeypatch.chdir(harness.root)
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", harness.ledger_url)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    result = CliRunner().invoke(cli.create_app(), ["history", "world"])
    assert result.exit_code == 0, result.output
    assert (
        f"{first.run_id} | run | finished | 1 | 22 | {first_version.sha256}"
        in result.output
    )
    assert (
        f"{second.run_id} | run | finished | 1 | 22 | {second_version.sha256}"
        in result.output
    )
    assert first_version.created_at.isoformat() in result.output
    assert second_version.created_at.isoformat() in result.output


@pytest.mark.parametrize("command", ["status", "history", "export"])
def test_reports_work_from_repository_subdirectory_without_api_key(
    saved_history: tuple[Path, str, str, str, str, str],
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, run1, _, _, _ = saved_history
    nested = root / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    arguments = [command, run1] if command in ("status", "export") else [command]
    result = CliRunner().invoke(cli.create_app(), arguments)
    assert result.exit_code == 0, result.output
    if command == "export":
        assert (root / f".specvania/runs/{run1}/events.jsonl").exists()
        assert not (nested / ".specvania").exists()


def test_export_refuses_symlink_escape_before_writing(
    saved_history: tuple[Path, str, str, str, str, str],
    tmp_path: Path,
) -> None:
    root, _, run1, _, _, _ = saved_history
    outside = tmp_path / "outside"
    outside.mkdir()
    runs = root / ".specvania/runs"
    runs.mkdir()
    (runs / run1).symlink_to(outside, target_is_directory=True)
    result = CliRunner().invoke(cli.create_app(), ["export", run1])
    assert result.exit_code == 1, result.output
    assert "inside .specvania/runs" in result.output
    assert list(outside.iterdir()) == []


def test_status_rejects_pricing_secret_without_partial_output(
    saved_history: tuple[Path, str, str, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_summary import pricing_file

    root, _, run1, _, _, _ = saved_history
    secret = "fake-control-token"
    monkeypatch.setenv("SPECVANIA_CONTROL_TOKEN", secret)
    path = pricing_file(root)
    path.write_text(path.read_text().replace("USD", secret).replace("priced", "w1"))
    result = CliRunner().invoke(cli.create_app(), ["status", run1])
    assert result.exit_code == 1, result.output
    assert secret not in result.output
    assert "Tokens in:" not in result.output
