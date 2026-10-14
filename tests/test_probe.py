"""T27 party probes: direct scripted provider, CLI and authenticated WebSocket."""

import asyncio
import json
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from test_studio_flow import prepare, receive, send, socket
from test_studio_flow import project as project
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.config import Config, RolesConfig
from specvania.provider import client as provider_module
from specvania.provider.client import (
    ChatMessage,
    Completion,
    ProbeModelResult,
    ProviderError,
)
from specvania.provider.fake import ProviderCall, ScriptedProvider
from specvania.run.events import RedactionGuard
from specvania.ui import studio as studio_module
from specvania.ui.studio import Studio

ORIGIN = "http://127.0.0.1:8765"


def bounded_json(ws: WebSocketTestSession) -> dict[str, Any]:
    async def read() -> dict[str, Any]:
        message = await asyncio.wait_for(ws._send_rx.receive(), timeout=1)
        ws._raise_on_close(message)
        return json.loads(message["text"])

    return ws.portal.call(read)


def bounded_receive(
    ws: WebSocketTestSession, kind: str, **fields: Any
) -> dict[str, Any]:
    for _ in range(20):
        message = bounded_json(ws)
        if message["kind"] == kind and all(
            message.get(key) == value for key, value in fields.items()
        ):
            return message
    pytest.fail(f"No {kind} message received")


class ModelProvider(ScriptedProvider):
    def __init__(
        self, replies: dict[str, Completion | ProviderError | Callable[[], Completion]]
    ) -> None:
        super().__init__([])
        self.replies = replies

    def complete(self, model: str, messages: Sequence[ChatMessage]) -> Completion:
        self.calls.append(ProviderCall(model=model, messages=tuple(messages)))
        reply = self.replies[model]
        if isinstance(reply, ProviderError):
            raise reply
        return reply() if callable(reply) else reply


def two_models() -> Config:
    return Config(
        roles=RolesConfig(mage=["one", "two"], warrior=["two"], paladin=["one"])
    )


def test_probe_models_overlap_without_waiting_for_each_other() -> None:
    entered = [ThreadEvent(), ThreadEvent()]
    release = ThreadEvent()

    def reply(index: int) -> Completion:
        entered[index].set()
        assert release.wait(3)
        return Completion(content="OK")

    provider = ModelProvider({"one": lambda: reply(0), "two": lambda: reply(1)})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            provider_module.probe_party, two_models(), provider, FakeClock()
        )
        try:
            assert all(event.wait(1) for event in entered), "Models did not overlap"
            assert not future.done()
        finally:
            release.set()
        assert [result.model for result in future.result(timeout=2)] == ["one", "two"]


def test_probe_callback_arrival_order_single_result_and_return_config_order() -> None:
    entered = [ThreadEvent(), ThreadEvent()]
    release = [ThreadEvent(), ThreadEvent()]
    delivered = ThreadEvent()
    seen: list[ProbeModelResult] = []

    def reply(index: int) -> Completion:
        entered[index].set()
        assert release[index].wait(3)
        return Completion(content="DO_NOT_EXPOSE")

    def on_result(result: ProbeModelResult) -> None:
        seen.append(result)
        delivered.set()

    provider = ModelProvider({"one": lambda: reply(0), "two": lambda: reply(1)})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            provider_module.probe_party,
            two_models(),
            provider,
            FakeClock(),
            on_result=on_result,
        )
        try:
            assert all(event.wait(1) for event in entered)
            release[1].set()
            assert delivered.wait(1)
            assert [result.model for result in seen] == ["two"]
            assert not future.done()
        finally:
            for event in release:
                event.set()
        results = future.result(timeout=2)
    assert [result.model for result in results] == ["one", "two"]
    assert [result.model for result in seen] == ["two", "one"]
    assert all(isinstance(result, ProbeModelResult) for result in seen)
    assert "DO_NOT_EXPOSE" not in str(seen)


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

    provider = ModelProvider(
        {
            "one": reply,
            "shared": ProviderError(status=404),
            "bad": ProviderError(status=401),
            "timeout": provider_module.ProviderTimeoutError(),
        }
    )
    seen: list[ProbeModelResult] = []
    results = provider_module.probe_party(
        settings(), provider, clock, on_result=seen.append
    )
    assert [r.model for r in results] == ["one", "shared", "bad", "timeout"]
    assert [r.status for r in results] == ["ok", 404, 401, "timeout"]
    assert results[0].latency_ms == 125
    assert len(provider.calls) == 4
    assert all(len(call.messages) == 1 for call in provider.calls)
    assert len({call.messages[0].content for call in provider.calls}) == 1
    assert "DO_NOT_EXPOSE" not in str(results)
    assert {r.model: r.status for r in seen} == {r.model: r.status for r in results}


def test_probe_connection_error_callback_keeps_classification() -> None:
    config = Config(roles=RolesConfig(mage=["one"], warrior=["one"], paladin=["one"]))
    seen: list[ProbeModelResult] = []
    results = provider_module.probe_party(
        config,
        ModelProvider({"one": ProviderError()}),
        FakeClock(),
        on_result=seen.append,
    )
    assert results == tuple(seen)
    assert results[0].status == "connection failed" and results[0].latency_ms is None


@pytest.mark.parametrize("failed", [False, True])
def test_doctor_probe_exit_and_one_line_per_distinct_model(
    project: Path, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    prepare(project)
    (project / ".specvania/config.toml").write_text(
        '[roles]\nmage=["one","shared"]\nwarrior=["shared"]\npaladin=["one"]\n'
    )
    provider = ModelProvider(
        {
            "one": Completion(content="DO_NOT_EXPOSE"),
            "shared": ProviderError(status=404)
            if failed
            else Completion(content="DO_NOT_EXPOSE"),
        }
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

    provider = ModelProvider({"one": reply, "shared": ProviderError(status=401)})
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
        result = receive(ws, "probe_result", done=True)
        assert result["results"] == [
            {"model": "one", "status": "ok", "latency_ms": 250.0},
            {"model": "shared", "status": 401, "latency_ms": None},
        ]
        assert instance.info() == before and len(provider.calls) == 2
        assert "DO_NOT_EXPOSE" not in str(result)
        assert instance.server.control_token not in str(result)


def test_blocked_probe_allows_connect_and_streams_each_result(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(project)
    (project / ".specvania/config.toml").write_text(
        '[roles]\nmage=["one","two"]\nwarrior=["two"]\npaladin=["one"]\n'
    )
    entered = [ThreadEvent(), ThreadEvent()]
    release = [ThreadEvent(), ThreadEvent()]
    connected, progressed = ThreadEvent(), ThreadEvent()
    received: list[dict[str, Any]] = []

    def reply(index: int) -> Completion:
        entered[index].set()
        assert release[index].wait(5)
        return Completion(content="DO_NOT_EXPOSE")

    provider = ModelProvider({"one": lambda: reply(0), "two": lambda: reply(1)})
    monkeypatch.setattr(
        studio_module, "OpenAIProvider", lambda *args, **kwargs: provider
    )
    instance = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    url = f"ws://127.0.0.1:8765/ws?view_token={instance.server.view_token}"
    with TestClient(instance.server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(url, headers={"Origin": ORIGIN}) as ws:
            ws.receive_json()
            send(ws, instance, "open_project", path=str(project))
            receive(ws, "studio_state", phase="project_open")

            def reconnect() -> None:
                with client.websocket_connect(url, headers={"Origin": ORIGIN}) as other:
                    received.append(bounded_json(other))
                    received.append(bounded_json(other))
                    pending = bounded_json(other)
                    assert pending["kind"] == "probe_result" and pending["models"] == [
                        "one",
                        "two",
                    ]
                    assert pending["results"] == [] and not pending["done"]
                    received.append(pending)
                    connected.set()
                    while True:
                        message = bounded_json(other)
                        received.append(message)
                        if (
                            message["kind"] == "probe_result"
                            and len(message["results"]) == 1
                        ):
                            progressed.set()
                        if message["kind"] == "probe_result" and message["done"]:
                            return

            with ThreadPoolExecutor(max_workers=2) as pool:
                send(ws, instance, "probe_party")
                future = None
                try:
                    assert entered[0].wait(1)
                    future = pool.submit(reconnect)
                    assert connected.wait(1), "Connecting blocked behind the probe"
                    assert [message["kind"] for message in received[:2]] == [
                        "state",
                        "studio_state",
                    ]
                    pending = pool.submit(
                        bounded_receive, ws, "probe_result", done=False
                    ).result(timeout=1)
                    assert (
                        pending["models"] == ["one", "two"] and pending["results"] == []
                    )
                    send(ws, instance, "probe_party")
                    rejection = pool.submit(
                        bounded_receive, ws, "approval_rejected"
                    ).result(timeout=1)
                    assert "active" in rejection["reason"]
                    assert entered[1].wait(1)
                    release[1].set()
                    assert progressed.wait(1), "Partial result was not streamed"
                    partial = next(
                        message
                        for message in received
                        if len(message.get("results", [])) == 1
                    )
                    assert (
                        partial["results"][0]["model"] == "two" and not partial["done"]
                    )
                    assert not release[0].is_set()
                finally:
                    for event in release:
                        event.set()
                assert future is not None
                future.result(timeout=2)
            assert [r["model"] for r in received[-1]["results"]] == ["one", "two"]
            assert instance.phase == "project_open"
            assert "DO_NOT_EXPOSE" not in str(received)


@pytest.mark.parametrize("callback", ["studio_snapshot", "studio_info"])
def test_slow_studio_connect_callback_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch, callback: str
) -> None:
    instance = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    entered, release = ThreadEvent(), ThreadEvent()
    original = getattr(instance.server, callback)

    def blocked() -> Any:
        entered.set()
        assert release.wait(5)
        return original()

    monkeypatch.setattr(instance.server, callback, blocked)
    with TestClient(instance.server.app, base_url=ORIGIN) as client:

        def connect() -> None:
            with client.websocket_connect(
                f"ws://127.0.0.1:8765/ws?view_token={instance.server.view_token}",
                headers={"Origin": ORIGIN},
            ) as ws:
                ws.receive_json()
                # Wait until the callback has finished before closing the socket.
                assert release.wait(5)

        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(connect)
            try:
                assert entered.wait(1)
                response = pool.submit(
                    client.get, "/", params={"view_token": instance.server.view_token}
                ).result(timeout=1)
                assert response.status_code == 200
            finally:
                release.set()
            future.result(timeout=2)


def test_completed_probe_restores_done_after_disconnect(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(project)
    (project / ".specvania/config.toml").write_text(
        '[roles]\nmage=["one"]\nwarrior=["one"]\npaladin=["one"]\n'
    )
    provider = ModelProvider({"one": Completion(content="DO_NOT_EXPOSE")})
    monkeypatch.setattr(
        studio_module, "OpenAIProvider", lambda *args, **kwargs: provider
    )
    instance = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        bounded_receive(ws, "studio_state", phase="project_open")
        send(ws, instance, "probe_party")
        completed = bounded_receive(ws, "probe_result", done=True)
    with socket(instance) as ws:
        assert bounded_json(ws)["kind"] == "studio_state"
        assert bounded_receive(ws, "probe_result", done=True) == completed


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
