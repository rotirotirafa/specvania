"""Scripted offline provider for tests and synthetic runs."""

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from types import TracebackType
from typing import Self

from specvania.provider.client import ChatMessage, Completion, ProviderError
from specvania.run.events import FrozenModel


class ProviderCall(FrozenModel):
    model: str
    messages: tuple[ChatMessage, ...]


class ScriptedProvider:
    def __init__(
        self,
        script: Iterable[Completion | ProviderError | Callable[[], Completion]],
        *,
        models: tuple[str, ...] = (),
    ) -> None:
        self._script = deque(script)
        self._models = models
        self.calls: list[ProviderCall] = []

    def complete(self, model: str, messages: Sequence[ChatMessage]) -> Completion:
        self.calls.append(ProviderCall(model=model, messages=tuple(messages)))
        if not self._script:
            raise AssertionError("Scripted provider has no remaining completion")
        result = self._script.popleft()
        if isinstance(result, ProviderError):
            raise result
        return result() if callable(result) else result

    def list_models(self) -> tuple[str, ...]:
        return self._models

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass
