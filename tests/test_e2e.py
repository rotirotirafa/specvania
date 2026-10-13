"""T22 offline CLI flow, shipping contracts and optional built-wheel evidence."""

import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.config import Config, ProviderConfig
from specvania.ledger.repo import Ledger
from specvania.provider.client import Completion
from specvania.provider.fake import ScriptedProvider
from specvania.run.events import RedactionGuard, Role
from specvania.run.jsonl import decode_events
from specvania.run.state import RoomState, fold_events


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.pg)])
def ledger_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgres":
        url = os.environ.get("SPECVANIA_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Postgres requires SPECVANIA_TEST_DATABASE_URL; CI runs G9")
        return url
    return f"sqlite:///{tmp_path / 'ledger.db'}"


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def reply(payload: object) -> Completion:
    return Completion(content=json.dumps(payload), tokens_in=2, tokens_out=3)


def test_cli_init_plan_run_history_export_end_to_end(
    tmp_path: Path,
    ledger_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Specvania test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    monkeypatch.chdir(root)
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", ledger_url)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    runner = CliRunner()
    app = cli.create_app()
    outputs: list[str] = []

    def invoke(*arguments: str) -> str:
        result = runner.invoke(app, list(arguments))
        assert result.exit_code == 0, (result.output, result.exception)
        outputs.append(result.output)
        return result.output

    invoke("init")
    assert (root / ".specvania/config.toml").is_file()
    assert ".specvania/ledger.db*" in (root / ".gitignore").read_text()
    assert ".specvania/runs/" in (root / ".gitignore").read_text()
    git(root, "add", ".")
    git(root, "commit", "-m", "initialize project")
    spec = (
        "# Greeting\n\n### S1 · Greeting is complete\n```gherkin\n"
        "Given a repository\nWhen both rooms clear\n"
        "Then greeting.txt and verified.txt contain the greeting\n```\n"
    )
    # Deliberately declare the dependent room first to prove door order.
    rooms = "## A1 · Greeting\nboss: S1\n\n"
    for room_id, dependency, path in (
        ("T02", "T01", "verified.txt"),
        ("T01", "none", "greeting.txt"),
    ):
        rooms += (
            f"### {room_id} · Write {path}\ndepends_on: {dependency}\n"
            f"covers: S1\nfiles: {path}\n\nChecklist:\n"
            f"- [ ] {path} contains the greeting\n\n"
        )
    mage = ScriptedProvider(
        [
            reply({"needs_clarification": False, "questions": []}),
            reply({"spec_md": spec, "tasks_md": rooms}),
        ]
    )
    script: list[Completion] = []
    for path in ("greeting.txt", "verified.txt"):
        script.extend(
            [
                reply(
                    {
                        "thought": "Write the greeting",
                        "action": "write_file",
                        "args": {"path": path, "content": "Hello, world!\n"},
                    }
                ),
                reply({"thought": "Ready for review", "action": "finish", "args": {}}),
                reply(
                    {
                        "verdict": "pass",
                        "checklist": [
                            {
                                "item": f"{path} contains the greeting",
                                "ok": True,
                                "note": "Greeting present in diff",
                            }
                        ],
                        "feedback": "Verified",
                    }
                ),
            ]
        )
    script.append(reply([{"scenario": "S1", "passed": True, "reason": "Verified"}]))
    party = ScriptedProvider(script)
    providers = iter((mage, party))
    keys: list[str] = []

    def provider_factory(config: ProviderConfig, *, api_key: str) -> ScriptedProvider:
        assert config == Config().provider
        keys.append(api_key)
        return next(providers)

    monkeypatch.setattr(cli, "OpenAIProvider", provider_factory)
    commands: list[list[str]] = []
    real_run = subprocess.run

    def recorded(command: list[str], **kwargs: Any) -> Any:
        if command[0] == "git":
            assert command[1:3] == ["-C", str(root)], "Git must stay in the temp repo"
            assert "push" not in command
            commands.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", recorded)
    invoke("plan", "Greeting")
    directory = root / ".specvania/specs/greeting"
    assert (directory / "spec.md").read_text() == spec
    assert (directory / "tasks.md").read_text() == rooms
    assert invoke("lint", str(directory)).strip() == "OK"
    git(root, "add", ".specvania/specs")
    git(root, "commit", "-m", "commit the world")
    base = git(root, "rev-parse", "HEAD")
    invoke("run", str(directory))

    with Ledger.open(ledger_url, clock=FakeClock(), guard=RedactionGuard()) as ledger:
        project = ledger.add_project(root)
        feature = ledger.list_features(project.id)[0]
        assert feature.slug == "greeting"
        runs = ledger.list_runs(feature.id)
        assert len(runs) == 2
        plan = next(row for row in runs if row.kind == "plan")
        run = next(row for row in runs if row.kind == "run")
        assert plan.status == run.status == "finished"
        assert plan.spec_version_id == run.spec_version_id
        version = ledger.get_spec_version(run.spec_version_id)
        assert (version.spec_md, version.tasks_md) == (spec, rooms)
        events = tuple(ledger.list_events(run.id))
        plan_events = tuple(ledger.list_events(plan.id))
    state = fold_events(events)
    assert state.status == "finished"
    assert {room.id: room.state for room in state.rooms} == {
        "T01": RoomState.CLEARED,
        "T02": RoomState.CLEARED,
    }
    assert [event.room_id for event in events if event.type == "room_entered"] == [
        "T01",
        "T02",
    ]
    cleared = [event for event in events if event.type == "room_cleared"]
    assert len(cleared) == 2
    assert git(root, "rev-list", "--count", f"{base}..HEAD") == "2"
    assert git(root, "rev-parse", "HEAD~2") == base
    assert git(root, "branch", "--show-current") == run.branch == f"specvania/{run.id}"
    for event, path in zip(cleared, ("greeting.txt", "verified.txt"), strict=True):
        assert git(root, "show", "-s", "--format=%s", event.commit) == (
            f"specvania({event.room_id}): Write {path}"
        )
        assert git(root, "show", f"{event.commit}:{path}") == "Hello, world!"
        assert (root / path).read_text() == "Hello, world!\n"
    assert git(root, "status", "--porcelain") == ""
    assert any(event.type == "boss_result" and event.passed for event in events)
    settings = Config()
    assert [call.model for call in mage.calls] == [settings.roles.mage[0]] * 2
    assert [call.model for call in party.calls] == [
        settings.roles.warrior[0],
        settings.roles.warrior[0],
        settings.roles.paladin[0],
        settings.roles.warrior[0],
        settings.roles.warrior[0],
        settings.roles.paladin[0],
        settings.roles.paladin[0],
    ]
    assert {event.role for event in events if event.type == "model_call"} == {
        Role.WARRIOR,
        Role.PALADIN,
    }
    # Reopen through the CLI with no provider key: history/export are local reads.
    key = os.environ["NVIDIA_API_KEY"]
    assert keys == [key, key]
    monkeypatch.delenv("NVIDIA_API_KEY")
    history = invoke("history", "greeting")
    assert f"{plan.id} | plan | finished | n/a | 10 | {version.sha256}" in history
    assert f"{run.id} | run | finished | 2 | 35 | {version.sha256}" in history
    assert version.created_at.isoformat() in history
    invoke("export", run.id)
    exported = root / f".specvania/runs/{run.id}/events.jsonl"
    assert decode_events(exported.read_text(), RedactionGuard()) == events
    summary = root / f".specvania/runs/{run.id}/summary.md"
    assert summary.is_file()
    assert commands and all("push" not in command for command in commands)
    artifacts = (
        "".join(outputs)
        + exported.read_text()
        + summary.read_text()
        + caplog.text
        + "".join(event.model_dump_json() for event in (*plan_events, *events))
    )
    assert key not in artifacts
    assert ledger_url not in artifacts


def repository() -> Path:
    return Path(__file__).resolve().parents[1]


def test_wheel_build_configuration_preserves_bundled_resources() -> None:
    root = repository()
    config = tomllib.loads((root / "pyproject.toml").read_text())
    assert config["build-system"] == {
        "requires": ["uv_build>=0.9,<0.14"],
        "build-backend": "uv_build",
    }
    # uv_build's default src layout includes package data. Reject exclusions or
    # a moved module root; the built-wheel test below proves the actual artifact.
    build = config.get("tool", {}).get("uv", {}).get("build-backend", {})
    assert build.get("module-root", "src") == "src"
    assert build.get("module-name", "specvania") == "specvania"
    assert not build.get("source-exclude")
    assert not build.get("wheel-exclude")
    for relative in (
        "ui/static/index.html",
        "ui/static/vendor/phaser.min.js",
        "ui/static/vendor/fonts/OFL.txt",
        "examples/demo/events.jsonl",
        "examples/demo/README.md",
        "ledger/migrations/env.py",
        "sdd/prompts/spec.md",
        "agent/prompts/warrior.md",
    ):
        assert (root / "src/specvania" / relative).is_file()


def test_built_wheel_contains_all_resources() -> None:
    root = repository()
    wheels = list((root / "dist").glob("specvania-*.whl"))
    if not wheels:
        pytest.skip(
            "No built wheel; CI wheel job runs uv build before this offline test"
        )
    assert len(wheels) == 1, "Inspect a clean dist directory with exactly one wheel"
    with ZipFile(wheels[0]) as wheel:
        for folder in (
            "ui/static",
            "examples",
            "ledger/migrations",
            "sdd/prompts",
            "agent/prompts",
        ):
            paths = [
                path
                for path in (root / "src/specvania" / folder).rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            ]
            assert paths, folder
            for path in paths:
                packaged = path.relative_to(root / "src").as_posix()
                assert packaged in wheel.namelist(), packaged
                assert wheel.read(packaged) == path.read_bytes(), packaged


def test_ci_builds_wheel_and_runs_postgres_end_to_end() -> None:
    jobs = json.loads((repository() / ".github/workflows/ci.yml").read_text())["jobs"]
    assert {"checks", "postgres", "wheel", "postgres-e2e"} <= jobs.keys()
    wheel = jobs["wheel"]
    assert wheel["runs-on"] == "ubuntu-latest"
    commands = [step["run"] for step in wheel["steps"] if "run" in step]
    assert commands == [
        "uv sync --locked",
        "uv build",
        "uv run pytest -q tests/test_e2e.py -k built_wheel",
        "uvx --from dist/*.whl specvania replay demo --help",
    ]
    assert not any(
        "node" in str(step).lower() or "docker" in str(step).lower()
        for step in wheel["steps"]
    )
    postgres = jobs["postgres-e2e"]
    assert postgres["runs-on"] == "ubuntu-latest"
    assert postgres["env"]["SPECVANIA_TEST_DATABASE_URL"] == (
        "postgresql+psycopg://specvania:specvania@localhost:5432/specvania"
    )
    commands = [step["run"] for step in postgres["steps"] if "run" in step]
    assert commands[:3] == [
        "docker compose up -d --wait postgres",
        "uv sync --locked --extra postgres",
        "uv run pytest -q -rs tests/test_e2e.py",
    ]
    assert postgres["steps"][-1] == {
        "name": "Stop ledger",
        "if": "always()",
        "run": "docker compose down",
    }


def section(document: str, heading: str) -> str:
    return document.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_readme_covers_quickstart_roles_team_mode_safety_and_prices() -> None:
    readme = (repository() / "README.md").read_text()
    quickstart = section(readme, "Python quickstart")
    for command in (
        "uv sync --locked",
        "uv run specvania replay demo",
        "uv tool install .",
        "uvx --from specvania specvania replay demo",
        "specvania init",
        'specvania plan "add greeting"',
        "git add .specvania/specs/add-greeting",
        "specvania run .specvania/specs/add-greeting --ui=browser",
        "specvania history add-greeting",
        "specvania export <run-id>",
    ):
        assert command in quickstart
    assert "Python 3.12" in quickstart and "neither Docker nor Node" in quickstart
    assert "synthetic" in quickstart and "no config" in quickstart
    mapping = section(readme, "SDD ↔ game")
    for concept in (
        "World",
        "Area",
        "Room",
        "Door",
        "Room cleared",
        "Boss",
        "Mage / Warrior / Paladin",
        "Stamina",
        "Cooldown",
        "Respawn",
        "Lives",
        "Coins",
        "Monster",
        "Ledger",
    ):
        assert f"| {concept} |" in mapping
    roles = section(readme, "Model roles and chains")
    for role in Role:
        row = next(
            line
            for line in roles.splitlines()
            if line.startswith(f"| {role.value.title()} |")
        )
        chain = " → ".join(
            f"`{model}`" for model in getattr(Config().roles, role.value)
        )
        assert chain in row
    assert "unverified" in roles and "O1/T23" in roles
    desktop = section(readme, "Optional desktop app")
    assert "apps/desktop/README.md" in desktop and "unsigned" in desktop
    assert "not notarized" in desktop and "--ui=auto" in desktop
    team = section(readme, "Optional Postgres team mode")
    assert "'.[postgres]'" in team
    assert "docker compose up -d --wait postgres" in team
    assert "SPECVANIA_DATABASE_URL='postgresql+psycopg://" in team
    safety = section(readme, "Safety")
    for requirement in (
        "D7",
        "D8",
        "D12",
        "D16",
        "never pushes",
        "first answer wins",
        "without an approval prompt",
        ".env*",
        "*.pem",
        "*.key",
        "id_*",
        "outside the repository root",
        "127.0.0.1",
        "view token",
        "Origin",
        "control token",
        "process argument",
        "--control-token",
    ):
        assert requirement in safety
    prices = section(readme, "Add reference prices")
    assert "No prices are shipped (D11)" in prices
    assert "per million tokens" in prices and "not priced" in prices
    for field in (
        "model",
        "input_per_mtok",
        "output_per_mtok",
        "currency",
        "source_url",
        "checked_on",
    ):
        assert f"{field} = " in prices
    gif = section(readme, "Real-run GIF")
    assert "placeholder" in gif and "T23" in gif and "real plan → run" in gif


def test_contributing_records_sdd_contract_and_all_gates() -> None:
    contributing = (repository() / "CONTRIBUTING.md").read_text()
    for path in (
        "AGENTS.md",
        ".agent/execution-protocol.md",
        ".agent/validation-gates.md",
        "docs/domain.md",
        "docs/conventions.md",
        "docs/spec.md",
        "docs/tasks.md",
        ".agent/deviations.md",
    ):
        assert f"]({path})" in contributing
    for gate in range(1, 11):
        assert f"| G{gate} |" in contributing
    for requirement in (
        "D1–D17",
        "one authorized room",
        "S-1–S-7",
        "next free DEV number",
        "newest first",
        "Approved by: pending",
        "never push",
        "socket guard",
        "feat(Txx): <room title in lowercase>",
    ):
        assert requirement in contributing


def test_notices_retain_vendored_asset_and_desktop_licenses() -> None:
    root = repository()
    notices = (root / "THIRD_PARTY_NOTICES.md").read_text()
    desktop = json.loads((root / "apps/desktop/package.json").read_text())
    assert "Phaser" in notices and "3.90.0" in notices
    assert "Press Start 2P" in notices and "SIL Open Font License" in notices
    assert "src/specvania/ui/static/vendor/fonts/OFL.txt" in notices
    assert "Electron" in notices and "LICENSES.chromium.html" in notices
    assert "electron-builder" in notices and "MIT" in notices
    for name in ("electron", "electron-builder"):
        assert desktop["devDependencies"][name] in notices
