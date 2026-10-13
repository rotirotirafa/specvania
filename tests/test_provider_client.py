import json
from functools import partial

import httpx2
import pytest
from openai import OpenAI

from specvania.config import ProviderConfig
from specvania.provider.client import ChatMessage, OpenAIProvider, ProviderError


@pytest.mark.parametrize("injected", [False, True])
@pytest.mark.parametrize("timeout_s", [300.0, 427.5])
def test_provider_timeout_reaches_sdk_requests(
    monkeypatch: pytest.MonkeyPatch, injected: bool, timeout_s: float
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"object": "list", "data": []})

    http = httpx2.Client(transport=httpx2.MockTransport(respond))
    sdk = OpenAI(api_key="fake-key", http_client=http) if injected else None
    if not injected:
        monkeypatch.setattr(
            "specvania.provider.client.OpenAI", partial(OpenAI, http_client=http)
        )
    config = (
        ProviderConfig() if timeout_s == 300 else ProviderConfig(timeout_s=timeout_s)
    )
    with OpenAIProvider(config, api_key="fake-key", sdk=sdk) as provider:
        assert provider.list_models() == ()
    assert len(requests) == 1
    assert requests[0].extensions["timeout"] == {
        "connect": timeout_s,
        "read": timeout_s,
        "write": timeout_s,
        "pool": timeout_s,
    }


def test_configured_endpoint_chat_usage_and_catalog() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx2.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "model-a",
                            "object": "model",
                            "created": 0,
                            "owned_by": "test",
                        }
                    ],
                },
            )
        return httpx2.Response(
            201,
            json={
                "id": "reply",
                "object": "chat.completion",
                "created": 0,
                "model": "model-a",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                },
            },
        )

    http = httpx2.Client(transport=httpx2.MockTransport(respond))
    sdk = OpenAI(api_key="fake-key", http_client=http)
    with OpenAIProvider(
        ProviderConfig(base_url="https://test.invalid/custom/v1"),
        api_key="fake-key",
        sdk=sdk,
    ) as provider:
        assert provider.list_models() == ("model-a",)
        reply = provider.complete(
            "model-a", (ChatMessage(role="user", content="question"),)
        )
        assert (reply.content, reply.tokens_in, reply.tokens_out, reply.status) == (
            "answer",
            11,
            4,
            201,
        )
    assert http.is_closed
    assert [str(request.url) for request in requests] == [
        "https://test.invalid/custom/v1/models",
        "https://test.invalid/custom/v1/chat/completions",
    ]
    payload = json.loads(requests[1].content)
    assert payload["model"] == "model-a"
    assert payload["messages"] == [{"role": "user", "content": "question"}]
    assert "tools" not in payload


@pytest.mark.parametrize("status", [429, 503, 401])
def test_http_errors_preserve_status_and_retry_after_without_sdk_retry(
    status: int,
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            status,
            headers={"Retry-After": "5"},
            json={"error": {"message": "fake-secret-prompt"}},
        )

    with OpenAIProvider(
        ProviderConfig(),
        api_key="fake-key",
        sdk=OpenAI(
            api_key="fake-key",
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        ),
    ) as provider:
        with pytest.raises(ProviderError) as failure:
            provider.complete(
                "model-a", (ChatMessage(role="user", content="question"),)
            )
    assert len(requests) == 1
    assert failure.value.status == status and failure.value.retry_after == "5"
    assert "fake-secret-prompt" not in str(failure.value)


@pytest.mark.parametrize("kind", [httpx2.ConnectError, httpx2.ReadTimeout])
def test_transport_errors_are_sanitized_and_not_retried(
    kind: type[httpx2.TransportError],
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        raise kind("fake-secret-key", request=request)

    with OpenAIProvider(
        ProviderConfig(),
        api_key="fake-key",
        sdk=OpenAI(
            api_key="fake-key",
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        ),
    ) as provider:
        with pytest.raises(ProviderError) as failure:
            provider.list_models()
    assert len(requests) == 1 and failure.value.status is None
    assert "fake-secret-key" not in str(failure.value)


def test_missing_usage_is_zero() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "reply",
                "object": "chat.completion",
                "created": 0,
                "model": "model-a",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "answer"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with OpenAIProvider(
        ProviderConfig(),
        api_key="fake-key",
        sdk=OpenAI(
            api_key="fake-key",
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        ),
    ) as provider:
        reply = provider.complete("model-a", ())
    assert reply.tokens_in == reply.tokens_out == 0


def test_empty_key_is_a_typed_error_without_network() -> None:
    with pytest.raises(ProviderError):
        OpenAIProvider(ProviderConfig(), api_key="  ")
