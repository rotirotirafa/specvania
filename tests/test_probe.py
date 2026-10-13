"""T27 party probes: direct scripted provider, CLI and authenticated WebSocket."""

import os
from pathlib import Path

import pytest
from test_studio_flow import prepare, receive, send, socket
from test_studio_flow import project as project
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.config import Config, RolesConfig
from specvania.provider import client as provider_module
from specvania.provider.client import Completion, ProviderError
from specvania.provider.fake import ScriptedProvider
from specvania.run.events import RedactionGuard
from specvania.ui import studio as studio_module
from specvania.ui.studio import Studio


def settings() -> Config:
    return Config(
        roles=RolesConfig(
            mage=["one", "shared"],
            warrior=["shared", "bad"],
            paladin=["timeout", "one"],
        )
    )


def test_probe_distinct_stable_models_latency_http_timeout_and_no_outputs() -> None:
    clock = FakeClock()

    def reply() -> Completion:
        clock.sleep(0.125)
        return Completion(content="DO_NOT_EXPOSE", tokens_in=999999)

    provider = ScriptedProvider(
        [
            reply,
            ProviderError(status=404),
            ProviderError(status=401),
            provider_module.ProviderTimeoutError(),
        ]
    )
    results = provider_module.probe_party(settings(), provider, clock)
    assert [r.model for r in results] == ["one", "shared", "bad", "timeout"]
    assert [r.status for r in results] == ["ok", 404, 401, "timeout"]
    assert results[0].latency_ms == 125
    assert len(provider.calls) == 4
    assert all(len(call.messages) == 1 for call in provider.calls)
    assert len({call.messages[0].content for call in provider.calls}) == 1
    assert "DO_NOT_EXPOSE" not in str(results)


@pytest.mark.parametrize("failed", [False, True])
def test_doctor_probe_exit_and_one_line_per_distinct_model(
    project: Path, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    prepare(project)
    (project / ".specvania/config.toml").write_text(
        '[roles]\nmage=["one","shared"]\nwarrior=["shared"]\npaladin=["one"]\n'
    )
    provider = ScriptedProvider(
        [
            Completion(content="DO_NOT_EXPOSE"),
            ProviderError(status=404)
            if failed
            else Completion(content="DO_NOT_EXPOSE"),
        ]
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    result = CliRunner().invoke(cli.create_app(), ["doctor", "--probe"])
    assert result.exit_code == (1 if failed else 0), result.output
    assert "one: ok" in result.output and "ms" in result.output
    assert ("shared: HTTP 404" if failed else "shared: ok") in result.output
    assert (
        len(
            [
                line
                for line in result.output.splitlines()
                if line.startswith(("one:", "shared:"))
            ]
        )
        == 2
    )
    assert (
        "DO_NOT_EXPOSE" not in result.output
        and os.environ["NVIDIA_API_KEY"] not in result.output
    )
    assert len(provider.calls) == 2


def test_doctor_without_probe_keeps_catalog_behavior(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(project)
    config = Config()
    models = tuple(
        dict.fromkeys(config.roles.mage + config.roles.warrior + config.roles.paladin)
    )
    provider = ScriptedProvider([], models=models)
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    result = CliRunner().invoke(cli.create_app(), ["doctor"])
    assert (
        result.exit_code == 0 and "mage:" in result.output and "found" in result.output
    )
    assert not provider.calls


def test_probe_party_websocket_control_required_same_results_and_no_state_change(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(project)
    (project / ".specvania/config.toml").write_text(
        '[roles]\nmage=["one","shared"]\nwarrior=["shared"]\npaladin=["one"]\n'
    )
    clock = FakeClock()

    def reply() -> Completion:
        clock.sleep(0.25)
        return Completion(content="DO_NOT_EXPOSE")

    provider = ScriptedProvider([reply, ProviderError(status=401)])
    monkeypatch.setattr(
        studio_module, "OpenAIProvider", lambda *args, **kwargs: provider
    )
    instance = Studio(guard=RedactionGuard(), clock=clock, port=8765)
    with socket(instance) as ws:
        ws.send_json({"kind": "probe_party"})
        assert receive(ws, "approval_rejected")["reason"] == "control unavailable"
        send(ws, instance, "probe_party")
        assert "phase" in receive(ws, "approval_rejected")["reason"]
        send(ws, instance, "open_project", path=str(project))
        receive(ws, "studio_state", phase="project_open")
        before = instance.info()
        ws.send_json({"kind": "probe_party"})
        assert receive(ws, "approval_rejected")["reason"] == "control unavailable"
        assert not provider.calls
        send(ws, instance, "probe_party")
        result = receive(ws, "probe_result")
        assert result["results"] == [
            {"model": "one", "status": "ok", "latency_ms": 250.0},
            {"model": "shared", "status": 401, "latency_ms": None},
        ]
        assert instance.info() == before and len(provider.calls) == 2
        assert "DO_NOT_EXPOSE" not in str(result)
        assert instance.server.control_token not in str(result)


@pytest.mark.parametrize("phase", ["planning", "running"])
def test_probe_party_rejects_active_plan_or_run(
    project: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    prepare(project)
    provider = ScriptedProvider([])
    monkeypatch.setattr(
        studio_module, "OpenAIProvider", lambda *args, **kwargs: provider
    )
    instance = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        receive(ws, "studio_state", phase="project_open")
        instance.phase = phase  # type: ignore[assignment]
        send(ws, instance, "probe_party")
        receive(ws, "approval_rejected")
        assert instance.phase == phase and not provider.calls
