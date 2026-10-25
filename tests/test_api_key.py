"""T50: fake app/shell keys cross every Python boundary without leaking."""

import logging
from pathlib import Path

import pytest
from test_doctor import configure
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.config import Config, ProviderConfig
from specvania.run.events import RedactionGuard, RunStarted, SecretLeakError
from specvania.run.jsonl import encode_events
from specvania.run.summary import render_summary, report_secrets, summarize
from specvania.ui.server import TokenLogFormatter
from specvania.ui.studio import Studio


@pytest.mark.parametrize(
    ("environment", "value", "source"),
    [
        (
            {"SPECVANIA_API_KEY": "fake-app", "CUSTOM_KEY": "fake-shell"},
            "fake-app",
            "app",
        ),
        ({"SPECVANIA_API_KEY": "", "CUSTOM_KEY": "fake-shell"}, "fake-shell", "env"),
        ({"CUSTOM_KEY": "fake-shell"}, "fake-shell", "env"),
        ({}, "", "none"),
    ],
)
def test_resolver_precedence_custom_env_and_empty_app(
    environment: dict[str, str], value: str, source: str
) -> None:
    from specvania.config import resolve_api_key

    resolved = resolve_api_key(ProviderConfig(api_key_env="CUSTOM_KEY"), environment)
    assert resolved.value == value
    assert resolved.source == source
    assert resolved.env_name == "CUSTOM_KEY"
    assert "fake-app" not in repr(resolved)
    assert "fake-shell" not in repr(resolved)


@pytest.mark.parametrize("app_value", ["fake-app-key", ""])
def test_doctor_reports_source_without_either_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_value: str
) -> None:
    configure(tmp_path, monkeypatch)
    monkeypatch.setenv("SPECVANIA_API_KEY", app_value)
    from specvania.provider.fake import ScriptedProvider

    received: list[str] = []

    def provider(config: ProviderConfig, *, api_key: str) -> ScriptedProvider:
        received.append(api_key)
        return ScriptedProvider([], models=("a", "b", "c"))

    monkeypatch.setattr(cli, "OpenAIProvider", provider)
    result = CliRunner().invoke(cli.create_app(), ["doctor"])
    assert result.exit_code == 0, result.output
    assert received == [app_value or "doctor-fake-secret"]
    assert f"API key: {'app' if app_value else 'CUSTOM_API_KEY'} set" in result.output
    assert "fake-app-key" not in result.output
    assert "doctor-fake-secret" not in result.output


def test_both_sources_guard_events_exports_summary_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPECVANIA_API_KEY", "fake-app-key")
    monkeypatch.setenv("CUSTOM_KEY", "fake-shell-key")
    config = Config(provider=ProviderConfig(api_key_env="CUSTOM_KEY"))
    guard = RedactionGuard()
    guard.register_api_keys(config.provider)
    secrets = report_secrets(config)
    assert "fake-app-key" in secrets and "fake-shell-key" in secrets
    for secret in ("fake-app-key", "fake-shell-key"):
        event = RunStarted(
            ts=FakeClock().now(), run_id="run", seq=1, feature=secret, branch="main"
        )
        with pytest.raises(SecretLeakError):
            guard.validate(event)
        with pytest.raises(SecretLeakError):
            encode_events([event], guard)
        with pytest.raises(SecretLeakError):
            render_summary(summarize([event]), secrets=secrets)
        assert secret not in guard.redact(f"provider returned {secret}")
        formatter = TokenLogFormatter((), guard=guard)
        record = logging.LogRecord(
            "provider", logging.WARNING, "", 0, "bad %s", (secret,), None
        )
        assert secret not in formatter.format(record)


def test_studio_uses_app_key_and_exposes_only_source_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from specvania.ui.server import StudioProject

    configure(tmp_path, monkeypatch)
    monkeypatch.setenv("SPECVANIA_API_KEY", "fake-app-key")
    instance = Studio(guard=RedactionGuard(), clock=FakeClock())
    instance._project = StudioProject(path=str(tmp_path), initialized=True)
    _, _, key = instance._settings()
    assert key == "fake-app-key"
    info = instance.info()
    assert info is not None
    assert info.model_dump()["api_key"] == {
        "source": "app",
        "env_name": "CUSTOM_API_KEY",
    }
    assert "fake-app-key" not in info.model_dump_json()
    assert "doctor-fake-secret" not in info.model_dump_json()
    assert "doctor-fake-secret" not in instance.guard.redact("doctor-fake-secret")
    instance.server.stop()


@pytest.mark.parametrize(
    "app_value, expected", [("fake-app-key", "fake-app-key"), ("", "fake-shell-key")]
)
def test_provider_resolves_app_or_custom_shell_key_at_sdk_boundary(
    monkeypatch: pytest.MonkeyPatch, app_value: str, expected: str
) -> None:
    import httpx2
    from openai import OpenAI

    from specvania.provider.client import OpenAIProvider

    monkeypatch.setenv("SPECVANIA_API_KEY", app_value)
    monkeypatch.setenv("CUSTOM_KEY", "fake-shell-key")
    headers: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        headers.append(request.headers["authorization"])
        return httpx2.Response(200, json={"object": "list", "data": []})

    http = httpx2.Client(transport=httpx2.MockTransport(respond))
    sdk = OpenAI(api_key="unused-injected-key", http_client=http)
    with OpenAIProvider(ProviderConfig(api_key_env="CUSTOM_KEY"), sdk=sdk) as provider:
        assert provider.list_models() == ()
    assert headers == [f"Bearer {expected}"]


@pytest.mark.parametrize("secret", ["fake-app-key", "fake-shell-key"])
def test_export_and_summary_refuse_historical_keys_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    from test_ledger import make_run

    from specvania.ledger import Ledger
    from specvania.run.summary import export_run, write_summary

    monkeypatch.setenv("SPECVANIA_API_KEY", "fake-app-key")
    monkeypatch.setenv("CUSTOM_KEY", "fake-shell-key")
    config = Config(provider=ProviderConfig(api_key_env="CUSTOM_KEY"))
    guard = RedactionGuard()
    with Ledger.open(
        f"sqlite:///{tmp_path / 'ledger.db'}", clock=FakeClock(), guard=guard
    ) as ledger:
        run_id = make_run(ledger, tmp_path)
        ledger.append_event(
            RunStarted(
                ts=FakeClock().now(),
                run_id=run_id,
                seq=0,
                feature=secret,
                branch="main",
            )
        )
        guard.register_api_keys(config.provider)
        with pytest.raises(SecretLeakError):
            export_run(tmp_path, ledger, run_id, guard, secrets=report_secrets(config))
        with pytest.raises(SecretLeakError):
            write_summary(tmp_path, ledger, run_id, config=config)
    assert not list(tmp_path.rglob("events.jsonl"))
    assert not list(tmp_path.rglob("summary.md"))


@pytest.mark.parametrize(
    "app_key, shell_key, source",
    [
        ("fake-app-key", "fake-shell-key", "app"),
        ("", "fake-shell-key", "env"),
        ("", "", "none"),
    ],
)
def test_websocket_key_status_contains_only_source_and_env_name(
    monkeypatch: pytest.MonkeyPatch, app_key: str, shell_key: str, source: str
) -> None:
    from starlette.testclient import TestClient

    monkeypatch.setenv("SPECVANIA_API_KEY", app_key)
    monkeypatch.setenv("NVIDIA_API_KEY", shell_key)
    instance = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    server = instance.server
    with TestClient(server.app, base_url="http://127.0.0.1:8765") as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
            headers={"Origin": "http://127.0.0.1:8765"},
        ) as ws:
            assert ws.receive_json()["kind"] == "state"
            ws.send_json(
                {
                    "kind": "set_language",
                    "language": "en",
                    "control_token": server.control_token,
                }
            )
            message = ws.receive_json()
            assert message["api_key"] == {
                "source": source,
                "env_name": "NVIDIA_API_KEY",
            }
            assert "fake-app-key" not in str(message)
            assert "fake-shell-key" not in str(message)


def test_summary_rejects_api_key_before_markdown_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "fake-app|quoted\\key"
    monkeypatch.setenv("SPECVANIA_API_KEY", secret)
    event = RunStarted(
        ts=FakeClock().now(), run_id="run", seq=1, feature=secret, branch="main"
    )
    with pytest.raises(SecretLeakError):
        render_summary(summarize([event]), secrets=report_secrets(Config()))
