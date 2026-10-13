"""T28: offline language contracts at the CLI and studio/provider boundaries."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from test_engine import git
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.config import ConfigError, initialize_config, load_config
from specvania.provider.client import Completion, ProviderError
from specvania.provider.fake import ProviderCall, ScriptedProvider
from specvania.run.events import RedactionGuard
from specvania.ui import studio as studio_module
from specvania.ui.server import StudioPhase
from specvania.ui.studio import Studio

ORIGIN = "http://127.0.0.1:8765"
SPEC = """# Iluminação do castelo

O grupo acende a lâmpada após a aprovação.

### S1 · Iluminação da sala
```gherkin
Given uma sala escura
When o grupo acende a lâmpada
Then a iluminação é verificada
```
"""
TASKS = """## A1 · Iluminação
boss: S1

### T01 · Acender a lâmpada
depends_on: none
covers: S1
files: lamp.py

O Guerreiro implementa a iluminação da sala.

Checklist:
- [ ] A lâmpada está acesa
"""


def draft() -> Completion:
    return Completion(content=json.dumps({"spec_md": SPEC, "tasks_md": TASKS}))


def clarify() -> Completion:
    return Completion(
        content=json.dumps(
            {
                "needs_clarification": True,
                "questions": [
                    {
                        "id": "C1",
                        "question": "Qual iluminação?",
                        "options": ["Lâmpada", "Tocha"],
                        "default": "Lâmpada",
                    }
                ],
            }
        )
    )


def action(name: str = "finish") -> Completion:
    args = (
        {"path": "lamp.py", "content": "lamp = True\n"} if name == "write_file" else {}
    )
    return Completion(
        content=json.dumps(
            {"thought": "Acendendo a lâmpada", "action": name, "args": args}
        )
    )


def review() -> Completion:
    return Completion(
        content=json.dumps(
            {
                "verdict": "pass",
                "checklist": [
                    {"item": "A lâmpada está acesa", "ok": True, "note": "Verificada"}
                ],
                "feedback": "Iluminação aprovada",
            }
        )
    )


def boss() -> Completion:
    return Completion(
        content=json.dumps(
            [{"scenario": "S1", "passed": True, "reason": "A lâmpada está acesa"}]
        )
    )


def assert_instruction(call: ProviderCall, language: str) -> None:
    system = "\n".join(
        message.content for message in call.messages if message.role == "system"
    )
    name = "English" if language == "en" else "Brazilian Portuguese (pt-BR)"
    assert system.count("Write human-facing text") == 1
    assert f"in {name}" in system
    for text in (
        "Clarify questions and options",
        "spec prose",
        "room titles and checklist items",
        "Warrior's `thought`",
        "Paladin's feedback",
        "code",
        "identifiers",
        "file paths",
        "commit message prefixes",
        "### T01 ·",
        "depends_on:",
        "covers:",
        "files:",
        "boss:",
        "Checklist:",
        "Gherkin keywords",
        "in English",
    ):
        assert text in system
    other = "Brazilian Portuguese (pt-BR)" if language == "en" else "in English;"
    assert other not in system


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Language Test")
    git(root, "config", "user.email", "language@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    initialize_config(root)
    path = root / ".specvania/config.toml"
    path.write_text(path.read_text().replace('language = "en"', 'language = "pt-BR"'))
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    return root


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_cli_language_reaches_every_role_and_repair_prompt(
    project: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    path = project / ".specvania/config.toml"
    path.write_text(
        path.read_text().replace('language = "pt-BR"', f'language = "{language}"')
    )
    git(project, "add", ".")
    git(project, "commit", "--allow-empty", "-m", "language")
    invalid = Completion(content="{}")
    provider = ScriptedProvider(
        [
            invalid,
            invalid,
            clarify(),
            invalid,
            invalid,
            draft(),
            invalid,
            invalid,
            action("write_file"),
            action(),
            invalid,
            invalid,
            review(),
            invalid,
            invalid,
            boss(),
        ]
    )
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    monkeypatch.chdir(project)
    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["plan", "lamp"], input="\n")
    assert result.exit_code == 0, result.output
    directory = project / ".specvania/specs/lamp"
    assert "Qual iluminação?" in (directory / "spec.md").read_text()
    assert "Lâmpada" in (directory / "spec.md").read_text()
    lint = runner.invoke(cli.create_app(), ["lint", str(directory)])
    assert lint.exit_code == 0 and "OK" in lint.output
    git(project, "add", ".")
    git(project, "commit", "-m", "map")
    result = runner.invoke(cli.create_app(), ["run", str(directory)])
    assert result.exit_code == 0, result.output
    assert len(provider.calls) == 16
    for call in provider.calls:
        assert_instruction(call, language)
    assert [call.model for call in provider.calls] == (
        ["moonshotai/kimi-k3"] * 6
        + ["z-ai/glm-5.3"] * 4
        + ["nvidia/nemotron-3-ultra-550b-a55b"] * 6
    )


def test_brazilian_portuguese_mage_world_passes_cli_lint(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider([clarify(), draft()])
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    monkeypatch.chdir(project)
    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["plan", "lamp"], input="\n")
    assert result.exit_code == 0, result.output
    directory = project / ".specvania/specs/lamp"
    assert (directory / "tasks.md").read_text() == TASKS
    assert SPEC.rstrip() in (directory / "spec.md").read_text()
    result = runner.invoke(cli.create_app(), ["lint", str(directory)])
    assert result.exit_code == 0 and "OK" in result.output
    for call in provider.calls:
        assert_instruction(call, "pt-BR")


@pytest.mark.parametrize(
    "value", ['"secret-unknown"', '"EN"', '"pt-br"', '" en "', "1", "true", "[]"]
)
def test_config_rejects_unknown_language_without_echo(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"[ui]\nlanguage = {value}\n")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "ui.language" in str(caught.value)
    assert "secret-unknown" not in str(caught.value)


def test_language_defaults_and_existing_ui_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[ui]\nhost = "127.0.0.1"\nport = 1234\nmode = "browser"\n')
    config = load_config(path)
    assert config.ui.language == "en"
    assert (config.ui.host, config.ui.port, config.ui.mode) == (
        "127.0.0.1",
        1234,
        "browser",
    )


@contextmanager
def socket(instance: Studio) -> Iterator[WebSocketTestSession]:
    with TestClient(instance.server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={instance.server.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            assert ws.receive_json()["kind"] == "state"
            yield ws


def send(ws: WebSocketTestSession, instance: Studio, kind: str, **fields: Any) -> None:
    ws.send_json(
        {"kind": kind, "control_token": instance.server.control_token, **fields}
    )


def receive(ws: WebSocketTestSession, kind: str, **fields: Any) -> dict[str, Any]:
    for _ in range(1000):
        message = ws.receive_json()
        assert not message.get("error"), message
        if message["kind"] == kind and all(
            message.get(key) == value for key, value in fields.items()
        ):
            return message
    pytest.fail("Expected language message missing")


@pytest.mark.parametrize(
    "phase", ["idle", "project_open", "planning", "world_ready", "running", "finished"]
)
def test_set_language_all_phases_requires_control_and_rejects_unknown(
    phase: StudioPhase, caplog: pytest.LogCaptureFixture
) -> None:
    instance = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    instance.phase = phase
    with socket(instance) as ws:
        ws.send_json(
            {
                "kind": "set_language",
                "language": "pt-BR",
                "control_token": instance.server.view_token,
            }
        )
        assert receive(ws, "approval_rejected")["reason"] == "control unavailable"
        send(ws, instance, "set_language", language="pt-BR")
        assert receive(ws, "studio_state", language="pt-BR")["phase"] == phase
        before = instance.info()
        for invalid in ("secret-unknown", "EN", "pt-br", " en ", 1, True, None):
            send(ws, instance, "set_language", language=invalid)
            assert receive(ws, "approval_rejected")["reason"] == "invalid message"
            assert instance.info() == before
        send(ws, instance, "set_language", language="en")
        assert receive(ws, "studio_state", language="en")["phase"] == phase
    assert "secret-unknown" not in caplog.text


def test_studio_override_during_clarify_reaches_next_mage_call(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider([clarify(), draft()])
    monkeypatch.setattr(
        studio_module, "OpenAIProvider", lambda *args, **kwargs: provider
    )
    instance = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        assert receive(ws, "studio_state", phase="project_open")["language"] == "pt-BR"
        send(ws, instance, "start_plan", idea="lamp")
        while not receive(ws, "studio_state", phase="planning").get("clarify_request"):
            pass
        send(ws, instance, "set_language", language="en")
        receive(ws, "studio_state", language="en")
        send(ws, instance, "clarify_answer", question_id="C1", answer="Lâmpada")
        receive(ws, "studio_state", phase="world_ready")
    assert_instruction(provider.calls[0], "pt-BR")
    assert_instruction(provider.calls[1], "en")
    assert load_config(project / ".specvania/config.toml").ui.language == "pt-BR"


def test_set_language_during_run_changes_next_call_and_preserves_sent_call(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = ThreadEvent(), ThreadEvent()

    def blocked() -> Completion:
        entered.set()
        assert release.wait(5)
        return action("write_file")

    provider = ScriptedProvider([draft(), blocked, action(), review(), boss()])
    monkeypatch.setattr(
        studio_module, "OpenAIProvider", lambda *args, **kwargs: provider
    )
    instance = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    try:
        with socket(instance) as ws:
            send(ws, instance, "open_project", path=str(project))
            receive(ws, "studio_state", phase="project_open")
            send(ws, instance, "start_plan", idea="lamp", no_clarify=True)
            receive(ws, "studio_state", phase="world_ready")
            send(ws, instance, "save_world", slug="lamp")
            receive(ws, "studio_state", phase="world_ready")
            send(ws, instance, "start_run", slug="lamp")
            assert entered.wait(5)
            assert_instruction(provider.calls[1], "pt-BR")
            send(ws, instance, "set_language", language="en")
            receive(ws, "studio_state", phase="running", language="en")
            assert_instruction(provider.calls[1], "pt-BR")
            release.set()
            receive(ws, "studio_state", phase="finished", language="en")
        with socket(instance) as ws:
            receive(ws, "studio_state", phase="finished", language="en")
        assert len(provider.calls) == 5
        for call in provider.calls[2:]:
            assert_instruction(call, "en")
        assert load_config(project / ".specvania/config.toml").ui.language == "pt-BR"
    finally:
        release.set()


def test_studio_override_before_project_survives_settings_and_fallback(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)

    def unavailable() -> Completion:
        # Change while this request is already sent: the fallback is a new call.
        from pydantic import TypeAdapter

        from specvania.ui.server import StudioControl

        instance.control(
            TypeAdapter(StudioControl).validate_python(
                {"kind": "set_language", "language": "pt-BR"}
            )
        )
        raise ProviderError(status=404)

    provider = ScriptedProvider([unavailable, draft()])
    monkeypatch.setattr(
        studio_module, "OpenAIProvider", lambda *args, **kwargs: provider
    )
    with socket(instance) as ws:
        send(ws, instance, "set_language", language="en")
        receive(ws, "studio_state", language="en")
        send(ws, instance, "open_project", path=str(project))
        receive(ws, "studio_state", phase="project_open", language="en")
        send(ws, instance, "start_plan", idea="lamp", no_clarify=True)
        receive(ws, "studio_state", phase="world_ready", language="pt-BR")
    assert_instruction(provider.calls[0], "en")
    assert_instruction(provider.calls[1], "pt-BR")
