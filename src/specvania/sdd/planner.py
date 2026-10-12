"""Mage planning with bounded lint repairs and success-only ledger persistence."""

import json
import re
import secrets
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from pydantic import Field, ValidationError, model_validator

from specvania.agent.sandbox import Sandbox, SandboxRefusal
from specvania.clock import Clock
from specvania.config import Config, NonEmptyText
from specvania.ledger.repo import Ledger
from specvania.provider.client import ChatMessage, Provider, ProviderError
from specvania.provider.jsonout import ModelOutputError, extract_json_value
from specvania.provider.router import Router
from specvania.run.events import (
    ClarifyAnswer,
    ClarifyQuestion,
    Event,
    FrozenModel,
    RedactionGuard,
    Role,
    SecretLeakError,
    SpecWritten,
)
from specvania.sdd.lint import WorldLintError, validate_directory
from specvania.sdd.world import WorldParseError


class PlanError(Exception):
    """A failed plan; the player can inspect the drafts and try again."""


class Question(FrozenModel):
    id: NonEmptyText
    question: NonEmptyText
    options: tuple[NonEmptyText, ...] | None = None
    default: NonEmptyText

    @model_validator(mode="after")
    def check_default(self) -> "Question":
        if self.options and self.default not in self.options:
            raise ValueError("The default must be one of the options")
        return self


class Clarification(FrozenModel):
    needs_clarification: bool
    questions: Annotated[tuple[Question, ...], Field(max_length=5)]

    @model_validator(mode="after")
    def check_questions(self) -> "Clarification":
        if self.needs_clarification != bool(self.questions):
            raise ValueError("Open decisions require between 1 and 5 questions")
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError("Question ids must be unique")
        return self


class Draft(FrozenModel):
    spec_md: str
    tasks_md: str


class PlanResult(FrozenModel):
    directory: Path
    run_id: str
    feature_id: str
    spec_version_id: str


def _prompt(name: str) -> str:
    return files("specvania.sdd").joinpath("prompts", name + ".md").read_text()


def _clarify(router: Router, context: dict[str, object]) -> Clarification:
    messages = [
        ChatMessage(role="system", content=_prompt("clarify")),
        ChatMessage(role="user", content=json.dumps(context)),
    ]
    for attempt in range(3):
        output = router.complete(Role.MAGE, messages).content
        try:
            return Clarification.model_validate_json(extract_json_value(output, "{"))
        except (ModelOutputError, ValidationError) as error:
            validation_error = str(error)
            if attempt == 2:
                raise PlanError(
                    "Clarify output is invalid after 2 repairs: " + validation_error
                ) from None
            messages.extend(
                [
                    ChatMessage(role="assistant", content=output),
                    ChatMessage(
                        role="user",
                        content=json.dumps({"validation_error": validation_error}),
                    ),
                ]
            )
    raise AssertionError("Unreachable Clarify repair state")


def _context(root: Path, sandbox: Sandbox) -> dict[str, object]:
    """Never descend through symlinks or send ignored/protected files."""
    resolve = sandbox.walk_resolver()
    entries: list[str] = []
    pending = [root]
    truncated = False
    while pending and not truncated:
        directory = pending.pop()
        children: list[Path] = []
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root).as_posix()
            try:
                resolved = resolve(relative)
            except SandboxRefusal:
                continue
            is_directory = resolved.is_dir()
            if len(entries) == 300:
                truncated = True
                break
            entries.append(relative + ("/" if is_directory else ""))
            if is_directory and not path.is_symlink():
                children.append(path)
        pending.extend(reversed(children))
    documents: dict[str, str] = {}
    for name in ("README.md", "docs/conventions.md", "docs/domain.md"):
        try:
            path = resolve(name)
        except SandboxRefusal:
            continue
        if path.is_file():
            documents[name] = path.read_text()
    return {
        "repo_map": entries,
        "repo_map_truncated": truncated,
        "documents": documents,
    }


def _log(spec: str, answers: list[dict[str, str]]) -> str:
    # Own the section so the model cannot invent or change the player's answers.
    lines = spec.splitlines(keepends=True)
    start = next(
        (i for i, line in enumerate(lines) if re.fullmatch(r"## Clarify log\s*", line)),
        None,
    )
    if start is not None:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        spec = "".join(lines[:start] + lines[end:])

    def cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", "<br>").replace("\r", "")

    log = "\n\n## Clarify log\n\n| # | Question | Answer |\n|---|---|---|\n"
    for answer in answers:
        log += (
            "| "
            + " | ".join(cell(answer[key]) for key in ("id", "question", "answer"))
            + " |\n"
        )
    return spec.rstrip() + log


def plan(
    *,
    root: Path,
    idea: str,
    config: Config,
    provider: Provider,
    ledger: Ledger,
    clock: Clock,
    guard: RedactionGuard,
    answer: Callable[[Question], str],
    no_clarify: bool = False,
) -> PlanResult:
    """Buffer seq=0 events; persist only a validated plan with kind='plan'.

    The run id is allocated before the first Mage call. Failed plans retain
    only drafts. Successful plans use an empty branch (no Git mutations).
    """
    if not idea.strip():
        raise PlanError("Give the Mage a non-empty idea.")
    root = root.resolve()
    run_id = f"{clock.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    slug = (
        re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:80].rstrip("-")
        or "feature"
    )
    buffered: list[Event] = []

    def emit(event: Event) -> None:
        guard.validate(event)
        buffered.append(event)

    router = Router(
        config=config, provider=provider, clock=clock, run_id=run_id, emit=emit
    )
    draft = Draft(spec_md="", tasks_md="")
    paths: dict[str, Path] = {}
    try:
        sandbox = Sandbox(root)
        directory = sandbox.resolve(f".specvania/specs/{slug}")
        directory.mkdir(parents=True, exist_ok=True)
        # Resolve each output independently; existing symlinks must obey D12 too.
        paths = {
            name: sandbox.resolve(f".specvania/specs/{slug}/{name}")
            for name in ("spec.md", "tasks.md", "spec.draft.md", "tasks.draft.md")
        }
        context = {"idea": idea, **_context(root, sandbox)}
        answers: list[dict[str, str]] = []
        if not no_clarify:
            clarification = _clarify(router, context)
            for question in clarification.questions:
                emit(
                    ClarifyQuestion(
                        ts=clock.now(),
                        run_id=run_id,
                        seq=0,
                        question_id=question.id[:200],
                        question=question.question[:200],
                        options=tuple(
                            option[:200] for option in question.options or ()
                        ),
                        default=question.default[:200],
                    )
                )
                chosen = answer(question).strip() or question.default
                emit(
                    ClarifyAnswer(
                        ts=clock.now(),
                        run_id=run_id,
                        seq=0,
                        question_id=question.id[:200],
                        answer=chosen[:200],
                    )
                )
                answers.append(
                    {"id": question.id, "question": question.question, "answer": chosen}
                )
        messages = [
            ChatMessage(role="system", content=_prompt("spec")),
            ChatMessage(
                role="user", content=json.dumps({**context, "clarify_answers": answers})
            ),
        ]
        with TemporaryDirectory(prefix=".plan-", dir=directory) as temporary:
            staging = Path(temporary)
            for attempt in range(3):
                reply = router.complete(Role.MAGE, messages)
                try:
                    draft = Draft.model_validate_json(
                        extract_json_value(reply.content, "{")
                    )
                    draft = (
                        Draft(
                            spec_md=_log(draft.spec_md, answers),
                            tasks_md=draft.tasks_md,
                        )
                        if answers
                        else draft
                    )
                    (staging / "spec.md").write_text(draft.spec_md)
                    (staging / "tasks.md").write_text(draft.tasks_md)
                    validate_directory(staging)
                    break
                except (WorldLintError, WorldParseError) as error:
                    diagnostics = (
                        error.diagnostics
                        if isinstance(error, WorldLintError)
                        else (error.diagnostic,)
                    )
                    lint_errors = [
                        f"{d.path.name}:{d.line} {d.rule} {d.message}"
                        for d in diagnostics
                    ]
                except (ModelOutputError, ValidationError) as error:
                    lint_errors = [str(error)]
                if attempt == 2:
                    raise PlanError(
                        "World is invalid after 2 repair attempts; "
                        "inspect spec.draft.md and tasks.draft.md. "
                        + "; ".join(lint_errors)
                    )
                messages.extend(
                    [
                        ChatMessage(role="assistant", content=reply.content),
                        ChatMessage(
                            role="user",
                            content=json.dumps(
                                {
                                    "lint_errors": lint_errors,
                                    "draft": draft.model_dump(),
                                }
                            ),
                        ),
                    ]
                )
        # Preflight summaries before creating records, including the feature slug
        # used by spec_written. Its version id will be generated by the ledger.
        for event in buffered:
            guard.validate(event)
        guard.validate(
            SpecWritten(
                ts=clock.now(), run_id=run_id, seq=0, feature=slug, spec_version=""
            )
        )
        paths["spec.md"].write_text(draft.spec_md)
        paths["tasks.md"].write_text(draft.tasks_md)
        paths["spec.draft.md"].unlink(missing_ok=True)
        paths["tasks.draft.md"].unlink(missing_ok=True)
    except (
        ProviderError,
        ValidationError,
        OSError,
        UnicodeError,
        SandboxRefusal,
        SecretLeakError,
        PlanError,
    ) as error:
        # A router error before step 2 still leaves an inspectable empty draft pair.
        if paths:
            try:
                paths["spec.draft.md"].write_text(draft.spec_md)
                paths["tasks.draft.md"].write_text(draft.tasks_md)
            except OSError:
                raise PlanError(
                    "Cannot write plan drafts; check repository permissions."
                ) from None
        if isinstance(error, PlanError):
            raise
        if isinstance(error, ProviderError):
            raise PlanError(str(error)) from None
        if isinstance(error, SecretLeakError):
            raise PlanError(
                "Plan event rejected: contains a registered secret."
            ) from None
        raise PlanError(
            "Cannot complete the plan; check Mage output and repository permissions."
        ) from None
    project = ledger.add_project(root)
    feature = ledger.add_feature(project.id, slug, idea)
    version = ledger.add_spec_version(feature.id, draft.spec_md, draft.tasks_md)
    ledger.create_run(feature.id, version.id, "", run_id=run_id, kind="plan")
    emit(
        SpecWritten(
            ts=clock.now(), run_id=run_id, seq=0, feature=slug, spec_version=version.id
        )
    )
    for event in buffered:
        ledger.append_event(event)
    return PlanResult(
        directory=directory,
        run_id=run_id,
        feature_id=feature.id,
        spec_version_id=version.id,
    )
