"""One validated Warrior action per turn, independent of provider tool calling."""

import json
from typing import Annotated, Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from specvania.run.events import FrozenModel

type NonEmptyText = Annotated[str, Field(min_length=1)]
type LineNumber = Annotated[int, Field(strict=True, ge=1)]

VALID_ACTIONS = (
    "read_file",
    "list_dir",
    "search",
    "write_file",
    "edit_file",
    "run_shell",
    "finish",
)


class ActionError(ValueError):
    """Invalid Warrior output, safe to include in a repair prompt or failure summary."""


class Arguments(FrozenModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )


class ReadFileArgs(Arguments):
    path: NonEmptyText
    start: LineNumber | None = None
    end: LineNumber | None = None

    @model_validator(mode="after")
    def ordered_range(self) -> "ReadFileArgs":
        if self.end is not None and self.end < (self.start or 1):
            raise ValueError(
                "end must follow start; line ranges are 1-based and inclusive"
            )
        return self


class PathArgs(Arguments):
    path: NonEmptyText


class SearchArgs(Arguments):
    pattern: str
    glob: str | None = None


class WriteFileArgs(PathArgs):
    content: str


class EditFileArgs(PathArgs):
    old: NonEmptyText
    new: str


class RunShellArgs(Arguments):
    command: NonEmptyText


class FinishArgs(Arguments):
    pass


class ActionModel(FrozenModel):
    thought: Annotated[str, Field(max_length=500)]

    @field_validator("thought", mode="before")
    @classmethod
    def cap_thought(cls, value: Any) -> Any:
        return value[:500] if isinstance(value, str) else value


class ReadFileAction(ActionModel):
    action: Literal["read_file"]
    args: ReadFileArgs


class ListDirAction(ActionModel):
    action: Literal["list_dir"]
    args: PathArgs


class SearchAction(ActionModel):
    action: Literal["search"]
    args: SearchArgs


class WriteFileAction(ActionModel):
    action: Literal["write_file"]
    args: WriteFileArgs


class EditFileAction(ActionModel):
    action: Literal["edit_file"]
    args: EditFileArgs


class RunShellAction(ActionModel):
    action: Literal["run_shell"]
    args: RunShellArgs


class FinishAction(ActionModel):
    action: Literal["finish"]
    args: FinishArgs


type Action = Annotated[
    ReadFileAction
    | ListDirAction
    | SearchAction
    | WriteFileAction
    | EditFileAction
    | RunShellAction
    | FinishAction,
    Field(discriminator="action"),
]

ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(
    Action, config=ConfigDict(hide_input_in_errors=True)
)


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActionError("JSON object keys must be unique.")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ActionError("JSON must not contain non-finite numbers.")


def _container_end(output: str, start: int) -> int:
    """Skip an entire container, including strings, even when its JSON is invalid."""
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(output)):
        char = output[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return index + 1
    raise ActionError("Return exactly one JSON object with thought, action and args.")


def _extract_object(output: str) -> dict[str, Any]:
    """Decode whole containers, so nested args never count as another action.

    Fences and surrounding prose are ignored. An array cannot wrap an action;
    malformed outer JSON cannot be bypassed by accepting a nested object.
    """
    decoder = json.JSONDecoder(
        object_pairs_hook=_unique_keys, parse_constant=_reject_constant
    )
    objects: list[dict[str, Any]] = []
    index = 0
    while index < len(output):
        if output[index] not in "{[":
            index += 1
            continue
        end = _container_end(output, index)
        try:
            value = decoder.decode(output[index:end])
        except json.JSONDecodeError:
            # Balanced prose such as [next action] is not a JSON object.
            # Never scan inside it and accidentally accept a nested action.
            index = end
            continue
        except RecursionError:
            raise ActionError(
                "Return exactly one JSON object with thought, action and args."
            ) from None
        if not isinstance(value, dict):
            raise ActionError(
                "Return exactly one JSON object, without an array wrapper."
            )
        objects.append(value)
        index = end
    if len(objects) != 1:
        raise ActionError(
            "Return exactly one JSON object with thought, action and args."
        )
    return objects[0]


def parse_action(output: str) -> Action:
    """Extract one object and validate the action discriminator and its arguments."""
    try:
        return ACTION_ADAPTER.validate_python(_extract_object(output))
    except ValidationError as error:
        errors = error.errors(
            include_input=False, include_url=False, include_context=False
        )
        if any(
            item["type"] in ("union_tag_invalid", "union_tag_not_found")
            for item in errors
        ):
            raise ActionError(
                "Valid actions: " + ", ".join(VALID_ACTIONS) + "."
            ) from None
        # Never echo model input values (including arbitrary extra-field names).
        known_fields = {
            "thought",
            "action",
            "args",
            "path",
            "start",
            "end",
            "pattern",
            "glob",
            "content",
            "old",
            "new",
            "command",
            *VALID_ACTIONS,
        }
        details = "; ".join(
            ".".join(
                str(part) if part in known_fields else "extra" for part in item["loc"]
            )
            + f": {item['msg']}"
            for item in errors
        )
        raise ActionError("Invalid action: " + details) from None


def summarize_args(action: Action) -> str:
    """Only metadata: shell commands and search patterns can embed file contents."""
    match action:
        case ReadFileAction(args=args):
            summary = f"path={args.path}; start={args.start}; end={args.end}"
        case ListDirAction(args=args):
            summary = f"path={args.path}"
        case WriteFileAction(args=args):
            summary = f"path={args.path}; content={len(args.content)} characters"
        case EditFileAction(args=args):
            summary = (
                f"path={args.path}; old={len(args.old)} characters; "
                f"new={len(args.new)} characters"
            )
        case SearchAction(args=args):
            summary = f"pattern={len(args.pattern)} characters; glob={args.glob}"
        case RunShellAction(args=args):
            summary = f"command={len(args.command)} characters"
        case FinishAction():
            summary = ""
    return summary[:200]
