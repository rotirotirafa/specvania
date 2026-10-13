"""Bounded synchronous Warrior attempts using the T08 tools and T05 router."""

import os
from collections.abc import Callable, Iterator
from importlib.resources import files
from pathlib import Path
from typing import Literal

from specvania.agent.actions import (
    Action,
    ActionError,
    EditFileAction,
    FinishAction,
    ListDirAction,
    ReadFileAction,
    RunShellAction,
    SearchAction,
    WriteFileAction,
    parse_action,
    summarize_args,
)
from specvania.agent.approval import ApprovalError
from specvania.agent.prompts import language_messages
from specvania.agent.sandbox import Sandbox, SandboxRefusal
from specvania.agent.tools import Observation, ToolError, Tools
from specvania.clock import Clock
from specvania.config import LimitsConfig
from specvania.provider.client import ChatMessage, ProviderError
from specvania.provider.router import Router
from specvania.run.events import Action as ActionEvent
from specvania.run.events import (
    Event,
    FrozenModel,
    NonNegativeInt,
    Role,
    ShortText,
)
from specvania.sdd.world import Room


class ContextFile(FrozenModel):
    path: str
    observation: Observation


class AttemptContext(FrozenModel):
    spec_summary: str
    room_block: str
    conventions: str
    domain: str
    repo_map: tuple[str, ...]
    room_files: tuple[ContextFile, ...]
    feedback: str


class AttemptResult(FrozenModel):
    """finish ends an attempt; only Paladin review and the engine clear a room."""

    status: Literal["finished", "failed"]
    steps: NonNegativeInt
    stop_reason: Literal["finish", "max_steps", "invalid_output", "router_error"]
    reason: ShortText


class Executor:
    """One instance can execute fresh attempts; history remains attempt-local.

    Inject tools bound to this run and room, and their same sandbox. As in the
    router/tools, emit accepts seq=0 events for the engine to append and publish.
    max_steps counts validated actions, including finish; repair calls do not
    perform actions and are bounded independently to two per turn.
    Attempt failures return their reason; room transitions belong to the engine.
    """

    def __init__(
        self,
        *,
        router: Router,
        tools: Tools,
        sandbox: Sandbox,
        limits: LimitsConfig,
        clock: Clock,
        run_id: str,
        emit: Callable[[Event], None],
    ) -> None:
        self._router = router
        self._tools = tools
        self._sandbox = sandbox
        self._limits = limits
        self._clock = clock
        self._run_id = run_id
        self._emit = emit

    def _repo_files(self) -> Iterator[str]:
        resolve = self._sandbox.walk_resolver()

        def failed(error: OSError) -> None:
            raise ToolError(
                "Cannot map repository; check directory permissions."
            ) from error

        for directory, dirs, names in os.walk(
            self._sandbox.root, followlinks=False, onerror=failed
        ):
            safe_dirs: list[str] = []
            for name in sorted(dirs):
                try:
                    resolve(str(Path(directory) / name))
                except SandboxRefusal:
                    continue
                safe_dirs.append(name)
            dirs[:] = safe_dirs
            for name in sorted(names):
                path = Path(directory) / name
                try:
                    resolve(str(path))
                except SandboxRefusal:
                    continue
                yield path.relative_to(self._sandbox.root).as_posix()

    def _read(self, path: str) -> Observation:
        try:
            return self._tools.read_file(path)
        except (SandboxRefusal, ToolError) as error:
            return Observation(output=str(error))

    def _context(self, room: Room, spec_summary: str, feedback: str) -> AttemptContext:
        repo_map: list[str] = []
        # Resolve requested room folders first. A protected folder never expands.
        folders: list[Path] = []
        selected = dict.fromkeys(room.files)
        for name in room.files:
            try:
                path = self._sandbox.resolve(name)
            except SandboxRefusal:
                continue
            if path.is_dir():
                folders.append(path)
                del selected[name]
        for name in self._repo_files():
            if len(repo_map) < 300:
                repo_map.append(name)
            path = self._sandbox.root / name
            if any(path.is_relative_to(folder) for folder in folders):
                selected[name] = None
        checklist = "\n".join(
            f"- [{'x' if item.checked else ' '}] {item.text}" for item in room.checklist
        )
        room_block = (
            f"### {room.id} · {room.title}\n"
            f"depends_on: {', '.join(room.depends_on) or 'none'}\n"
            f"covers: {', '.join(room.covers) or 'none'}\n"
            f"files: {', '.join(room.files)}\n\n{room.context}\n\n"
            f"Checklist:\n{checklist}"
        )
        return AttemptContext(
            spec_summary=spec_summary,
            room_block=room_block,
            conventions=self._read("docs/conventions.md").output,
            domain=self._read("docs/domain.md").output,
            repo_map=tuple(repo_map),
            room_files=tuple(
                ContextFile(path=name, observation=self._read(name))
                for name in selected
            ),
            feedback=feedback,
        )

    def _dispatch(self, action: Action) -> Observation:
        try:
            match action:
                case ReadFileAction(args=args):
                    return self._tools.read_file(args.path, args.start, args.end)
                case ListDirAction(args=args):
                    return self._tools.list_dir(args.path)
                case SearchAction(args=args):
                    return self._tools.search(args.pattern, args.glob)
                case WriteFileAction(args=args):
                    return self._tools.write_file(args.path, args.content)
                case EditFileAction(args=args):
                    return self._tools.edit_file(args.path, args.old, args.new)
                case RunShellAction(args=args):
                    return self._tools.run_shell(args.command)
                case FinishAction():
                    return Observation(output="finished")
        except (ToolError, SandboxRefusal, ApprovalError) as error:
            return Observation(output=str(error))

    @staticmethod
    def _failed(
        steps: int,
        stop_reason: Literal["max_steps", "invalid_output", "router_error"],
        reason: str,
    ) -> AttemptResult:
        return AttemptResult(
            status="failed", steps=steps, stop_reason=stop_reason, reason=reason[:200]
        )

    def execute(
        self, room: Room, *, spec_summary: str, feedback: str = ""
    ) -> AttemptResult:
        self._router.start_room(room.id)
        prompt = (
            files("specvania.agent")
            .joinpath("prompts/warrior.md")
            .read_text(encoding="utf-8")
        )
        system_prompt, _, repair_prompt = prompt.partition("<!-- repair -->")
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(
                role="user",
                content=self._context(room, spec_summary, feedback).model_dump_json(),
            ),
        ]
        for step in range(self._limits.max_steps):
            action: Action | None = None
            for repair in range(3):
                try:
                    output = self._router.complete(
                        Role.WARRIOR,
                        lambda: language_messages(messages, self._router.ui.language),
                    ).content
                except ProviderError as error:
                    return self._failed(step, "router_error", str(error))
                messages.append(ChatMessage(role="assistant", content=output))
                try:
                    action = parse_action(output)
                except ActionError as error:
                    if repair == 2:
                        return self._failed(
                            step,
                            "invalid_output",
                            f"Invalid Warrior output after 2 repair prompts: {error}",
                        )
                    messages.append(
                        ChatMessage(
                            role="user",
                            content=repair_prompt.replace(
                                "{validation_error}", str(error)
                            ),
                        )
                    )
                    continue
                break
            assert action is not None
            self._emit(
                ActionEvent(
                    ts=self._clock.now(),
                    run_id=self._run_id,
                    seq=0,
                    room_id=room.id,
                    thought=action.thought,
                    action=action.action,
                    args_summary=summarize_args(action),
                )
            )
            if isinstance(action, FinishAction):
                return AttemptResult(
                    status="finished",
                    steps=step + 1,
                    stop_reason="finish",
                    reason="Warrior finished the attempt; awaiting Paladin review.",
                )
            messages.append(
                ChatMessage(
                    role="user", content=self._dispatch(action).model_dump_json()
                )
            )
        return self._failed(
            self._limits.max_steps,
            "max_steps",
            f"Warrior reached max_steps={self._limits.max_steps} without finish.",
        )
