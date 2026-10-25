"""Synchronous OpenAI-compatible port; retries and timing belong to the router."""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import TracebackType
from typing import Literal, Protocol, Self

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import Field

from specvania.clock import Clock
from specvania.config import Config, ProviderConfig, resolve_api_key
from specvania.run.events import FrozenModel, NonNegativeInt


class ChatMessage(FrozenModel):
    role: Literal["system", "user", "assistant"]
    content: str


class Completion(FrozenModel):
    content: str
    tokens_in: NonNegativeInt = 0
    tokens_out: NonNegativeInt = 0
    status: int = Field(default=200, ge=200, le=299)


class ProviderError(Exception):
    """Sanitized failure. None means no HTTP response was received.

    The router records transport failures as synthetic status 599 to preserve
    T03's required integer status field; 599 is not a provider HTTP response.
    Provider response bodies, prompts, credentials and URLs are never included.
    """

    def __init__(
        self, *, status: int | None = None, retry_after: str | None = None
    ) -> None:
        self.status = status
        self.retry_after = retry_after
        super().__init__(
            f"Provider call failed (HTTP {status}); check provider configuration."
            if status is not None
            else "Provider connection failed; check connectivity and endpoint."
        )

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status == 429 or 500 <= self.status <= 599


class Provider(Protocol):
    def complete(self, model: str, messages: Sequence[ChatMessage]) -> Completion: ...

    def list_models(self) -> tuple[str, ...]: ...


class ProviderTimeoutError(ProviderError):
    """A request reached the configured provider timeout without a response."""


class ProbeModelResult(FrozenModel):
    model: str
    status: int | Literal["ok", "timeout", "connection failed"]
    latency_ms: float | None = None


def probe_party(
    config: Config,
    provider: Provider,
    clock: Clock,
    *,
    on_result: Callable[[ProbeModelResult], None] | None = None,
) -> tuple[ProbeModelResult, ...]:
    """One minimal request per distinct model; discard all completion contents."""
    models = dict.fromkeys(
        config.roles.mage + config.roles.warrior + config.roles.paladin
    )

    def probe(model: str) -> ProbeModelResult:
        started = clock.now()
        try:
            provider.complete(
                model, (ChatMessage(role="user", content="Reply with OK."),)
            )
        except ProviderTimeoutError:
            result = ProbeModelResult(model=model, status="timeout")
        except ProviderError as error:
            result = ProbeModelResult(
                model=model,
                status=error.status
                if error.status is not None
                else "connection failed",
            )
        else:
            result = ProbeModelResult(
                model=model,
                status="ok",
                latency_ms=max(0.0, (clock.now() - started).total_seconds() * 1000),
            )
        return result

    results: dict[str, ProbeModelResult] = {}
    with ThreadPoolExecutor(
        max_workers=len(models), thread_name_prefix="specvania-probe"
    ) as pool:
        futures = [pool.submit(probe, model) for model in models]
        for future in as_completed(futures):
            result = future.result()
            results[result.model] = result
            if on_result is not None:
                on_result(result)
    return tuple(results[model] for model in models)


class OpenAIProvider:
    """Own one SDK client per key. Injection supports offline mock transports.

    SDK retries are always disabled, including on an injected SDK, so each
    router attempt produces exactly one request, one limiter debit and one event.
    """

    def __init__(
        self,
        config: ProviderConfig,
        *,
        api_key: str | None = None,
        sdk: OpenAI | None = None,
    ) -> None:
        if api_key is None:
            api_key = resolve_api_key(config).value
        if not api_key.strip():
            raise ProviderError(status=401)
        self._sdk = sdk or OpenAI(
            api_key=api_key,
            base_url=config.base_url,
            max_retries=0,
            timeout=config.timeout_s,
        )
        self._sdk.base_url = config.base_url
        self._sdk.api_key = api_key
        self._sdk.max_retries = 0
        self._sdk.timeout = config.timeout_s

    def complete(self, model: str, messages: Sequence[ChatMessage]) -> Completion:
        payload: list[ChatCompletionMessageParam] = []
        for message in messages:
            match message.role:
                case "system":
                    payload.append({"role": "system", "content": message.content})
                case "user":
                    payload.append({"role": "user", "content": message.content})
                case "assistant":
                    payload.append({"role": "assistant", "content": message.content})
        try:
            response = self._sdk.chat.completions.with_raw_response.create(
                model=model, messages=payload
            )
            reply = response.parse()
            if not reply.choices:
                raise ProviderError(status=response.status_code)
            usage = reply.usage
            return Completion(
                content=reply.choices[0].message.content or "",
                tokens_in=usage.prompt_tokens if usage else 0,
                tokens_out=usage.completion_tokens if usage else 0,
                status=response.status_code,
            )
        except APIStatusError as error:
            raise ProviderError(
                status=error.status_code,
                retry_after=error.response.headers.get("Retry-After"),
            ) from None
        except APITimeoutError:
            raise ProviderTimeoutError() from None
        except APIConnectionError:
            raise ProviderError() from None
        except APIError:
            raise ProviderError() from None

    def list_models(self) -> tuple[str, ...]:
        try:
            # OpenAI-compatible catalogs return a single list, without paging.
            return tuple(model.id for model in self._sdk.models.list().data)
        except APIStatusError as error:
            raise ProviderError(
                status=error.status_code,
                retry_after=error.response.headers.get("Retry-After"),
            ) from None
        except APIError:
            raise ProviderError() from None

    def close(self) -> None:
        self._sdk.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
