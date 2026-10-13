"""Resource-backed language instructions, freshly built for each model call."""

from collections.abc import Sequence
from importlib.resources import files

from specvania.config import Language
from specvania.provider.client import ChatMessage


def language_messages(
    messages: Sequence[ChatMessage], language: Language
) -> tuple[ChatMessage, ...]:
    """Keep history intact and snapshot the current language for this request."""
    name = "English" if language == "en" else "Brazilian Portuguese (pt-BR)"
    instruction = (
        files("specvania.agent.prompts")
        .joinpath("language.md")
        .read_text(encoding="utf-8")
        .replace("{language}", name)
    )
    system = messages[0].model_copy(
        update={"content": messages[0].content.rstrip() + "\n\n" + instruction}
    )
    return (system, *messages[1:])
