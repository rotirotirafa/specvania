"""Paladin room and boss judgments over explicit, bounded evidence."""

import json
import re
import shlex
import unicodedata
from collections.abc import Callable, Sequence
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, Field, RootModel, ValidationError

from specvania.agent.gates import GateExecution
from specvania.agent.sandbox import Sandbox, SandboxRefusal
from specvania.clock import Clock
from specvania.provider.client import ChatMessage
from specvania.provider.router import Router
from specvania.run.events import (
    BossResult,
    BossScenario,
    ChecklistResult,
    Event,
    FrozenModel,
    GateResult,
    Review,
    Role,
)
from specvania.sdd.world import Area, Room, Scenario


class ReviewError(Exception):
    """Paladin evidence or output is invalid; no successful verdict is emitted."""


class ItemReview(FrozenModel):
    item: str
    ok: bool = Field(strict=True)
    note: str


class RoomReview(FrozenModel):
    verdict: Literal["pass", "fail"]
    checklist: tuple[ItemReview, ...]
    feedback: str


class ScenarioReview(FrozenModel):
    scenario: str
    passed: bool = Field(strict=True)
    reason: str


class BossReview(RootModel[tuple[ScenarioReview, ...]]):
    pass


def _summary(text: str) -> str:
    return text if len(text) <= 200 else text[:188] + " [truncated]"


def _prompt(name: str) -> str:
    return (
        files("specvania.agent").joinpath("prompts", name).read_text(encoding="utf-8")
    )


def _extract_json(output: str, shape: Literal["{", "["]) -> str:
    """Find one complete top-level value, never its nested objects or arrays."""
    candidates: list[str] = []
    stack: list[str] = []
    start = 0
    quoted = escaped = False
    for index, character in enumerate(output):
        if not stack:
            if character in "{[":
                start = index
                stack.append(character)
                quoted = escaped = False
            continue
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "{[":
            stack.append(character)
        elif character in "}]":
            if stack.pop() != ("{" if character == "}" else "["):
                raise ReviewError("Invalid JSON: mismatched brackets.")
            if not stack:
                candidate = output[start : index + 1]
                try:
                    json.loads(candidate)
                except ValueError:
                    continue
                candidates.append(candidate)
    if stack:
        raise ReviewError("Invalid JSON: incomplete top-level value.")
    if len(candidates) != 1 or not candidates[0].startswith(shape):
        kind = "object" if shape == "{" else "array"
        raise ReviewError(f"Expected exactly one top-level JSON {kind}.")
    return candidates[0]


def _normalize_item(text: str) -> str:
    text = re.sub(r"^\s*(?:[-*+]\s*)?\[\s*[xX]?\s*\]\s*", "", text)
    text = " ".join(text.casefold().split())
    while text and unicodedata.category(text[-1]).startswith("P"):
        text = text[:-1].rstrip()
    return text


def _match_checklist(
    room: Room, results: tuple[ItemReview, ...]
) -> tuple[ItemReview, ...]:
    """Consume each judgment once; preserve original items and room order."""
    matches: dict[int, ItemReview] = {}
    unused = list(results)
    for index, item in enumerate(room.checklist):
        for result_index, result in enumerate(unused):
            if _normalize_item(result.item) == _normalize_item(item.text):
                matches[index] = unused.pop(result_index)
                break
    if len(results) == len(room.checklist):
        # Keep recognized matches; pair remaining judgments in their order.
        remaining = [i for i in range(len(room.checklist)) if i not in matches]
        matches.update(zip(remaining, unused, strict=True))
    return tuple(
        ItemReview(
            item=item.text,
            ok=matches[index].ok if index in matches else False,
            note=matches[index].note
            if index in matches
            else "not reviewed by the Paladin",
        )
        for index, item in enumerate(room.checklist)
    )


def _gate_result(gate: GateResult | GateExecution) -> GateResult:
    return gate.result if isinstance(gate, GateExecution) else gate


def _gate_payload(
    gates: Sequence[GateResult | GateExecution],
) -> list[dict[str, object]]:
    return [
        {
            **_gate_result(gate).model_dump(mode="json"),
            "output_tail": gate.output_tail
            if isinstance(gate, GateExecution)
            else gate.output_summary,
        }
        for gate in gates
    ]


class Reviewer:
    """Never load repository file contents: evidence is the supplied diff.

    Both old and new diff paths pass the existing Sandbox policy (D12).
    Emit callbacks accept seq=0; the engine appends to the Ledger then bus.
    Router errors propagate to the engine, which owns pauses and respawns.
    """

    def __init__(
        self,
        *,
        router: Router,
        sandbox: Sandbox,
        clock: Clock,
        run_id: str,
        emit: Callable[[Event], None],
    ) -> None:
        self._router = router
        self._sandbox = sandbox
        self._clock = clock
        self._run_id = run_id
        self._emit = emit

    def _safe_diff(self, diff: str) -> str:
        """Filter whole Git diff sections, including rename/delete paths.

        Reject unstructured evidence instead of accidentally forwarding file
        contents without a path. Git's quoted paths and ordinary spaces work.
        """
        sections: list[str] = []
        current: list[str] = []
        allowed = False
        for line in diff.splitlines(keepends=True):
            if line.startswith("diff --git "):
                if allowed:
                    sections.extend(current)
                current = [line]
                header = line[len("diff --git ") :].strip()
                try:
                    paths = shlex.split(header)
                except ValueError:
                    raise ReviewError("Invalid Git diff path header.") from None
                if len(paths) != 2:
                    old, separator, new = header.partition(" b/")
                    paths = [old, "b/" + new] if separator else []
                if (
                    len(paths) != 2
                    or not paths[0].startswith("a/")
                    or not paths[1].startswith("b/")
                ):
                    raise ReviewError("Expected Git diff old and new paths.")
                try:
                    for path in paths:
                        self._sandbox.resolve(path[2:])
                except SandboxRefusal:
                    allowed = False
                else:
                    allowed = True
            elif current:
                current.append(line)
            elif line.strip():
                raise ReviewError("Expected a Git diff with repository path headers.")
        if allowed:
            sections.extend(current)
        return "".join(sections)

    def _complete[Result: BaseModel](
        self,
        prompt: str,
        payload: dict[str, object],
        model: type[Result],
        shape: Literal["{", "["],
        validate: Callable[[Result], None] | None = None,
    ) -> Result:
        messages = [
            ChatMessage(role="system", content=_prompt(prompt)),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]
        for attempt in range(3):
            output = self._router.complete(Role.PALADIN, messages).content
            try:
                result = model.model_validate_json(_extract_json(output, shape))
                if validate is not None:
                    validate(result)
                return result
            except (ReviewError, ValidationError) as error:
                validation_error = str(error)
                if attempt == 2:
                    raise ReviewError(
                        f"Paladin output is invalid after 2 repairs: {validation_error}"
                    ) from None
                messages.extend(
                    (
                        ChatMessage(role="assistant", content=output),
                        ChatMessage(
                            role="user",
                            content=json.dumps({"validation_error": validation_error}),
                        ),
                    )
                )
        raise AssertionError("Unreachable Paladin repair state")

    def review_room(
        self,
        room: Room,
        *,
        attempt_diff: str,
        gates: Sequence[GateResult | GateExecution],
    ) -> RoomReview:
        """Match judgments to the room; missing items and failed gates override pass."""
        room_block = {
            "id": room.id,
            "title": room.title,
            "area_id": room.area_id,
            "depends_on": room.depends_on,
            "covers": room.covers,
            "files": room.files,
            "context": room.context,
            "checklist": [item.text for item in room.checklist],
        }
        review = self._complete(
            "paladin_room.md",
            {
                "room": room_block,
                "diff": self._safe_diff(attempt_diff),
                "gate_results": _gate_payload(gates),
            },
            RoomReview,
            "{",
        )
        review = review.model_copy(
            update={"checklist": _match_checklist(room, review.checklist)}
        )
        failed_gates = [gate for gate in gates if _gate_result(gate).exit_code != 0]
        if any(not item.ok for item in review.checklist) or failed_gates:
            review = review.model_copy(update={"verdict": "fail"})
        if failed_gates:
            diagnostics = [
                f"Gate failed ({_gate_result(gate).command}):\n"
                + (
                    gate.output_tail
                    if isinstance(gate, GateExecution)
                    else gate.output_summary
                )
                for gate in failed_gates
            ]
            review = review.model_copy(
                update={"feedback": "\n\n".join((review.feedback, *diagnostics))}
            )
        self._emit(
            Review(
                ts=self._clock.now(),
                run_id=self._run_id,
                seq=0,
                room_id=room.id,
                verdict=review.verdict,
                checklist=tuple(
                    ChecklistResult(
                        item=_summary(item.item), ok=item.ok, note=_summary(item.note)
                    )
                    for item in review.checklist
                ),
                feedback=_summary(review.feedback),
            )
        )
        return review

    def evaluate_boss(
        self,
        area: Area,
        *,
        scenarios: Sequence[Scenario],
        area_diff: str,
        gates: Sequence[GateResult | GateExecution],
    ) -> tuple[BossScenario, ...]:
        """Validate all judgments before emitting one scenario event per boss id."""
        selected = [scenario for scenario in scenarios if scenario.id in area.boss]
        if sorted(s.id for s in selected) != sorted(area.boss):
            raise ReviewError("Provide every boss scenario exactly once.")

        def validate(review: BossReview) -> None:
            if sorted(r.scenario for r in review.root) != sorted(area.boss):
                raise ReviewError(
                    "Paladin must evaluate every boss scenario exactly once."
                )

        reviews = self._complete(
            "paladin_boss.md",
            {
                "area": {"id": area.id, "title": area.title, "boss": area.boss},
                "scenarios": [
                    {"id": s.id, "title": s.title, "gherkin": s.gherkin}
                    for s in selected
                ],
                "diff": self._safe_diff(area_diff),
                "gate_results": _gate_payload(gates),
            },
            BossReview,
            "[",
            validate,
        ).root
        by_id = {review.scenario: review for review in reviews}
        results: list[BossScenario] = []
        for scenario_id in area.boss:
            review = by_id[scenario_id]
            result = BossScenario(
                ts=self._clock.now(),
                run_id=self._run_id,
                seq=0,
                area_id=area.id,
                scenario=scenario_id,
                passed=review.passed,
                reason=_summary(review.reason),
            )
            self._emit(result)
            results.append(result)
        self._emit(
            BossResult(
                ts=self._clock.now(),
                run_id=self._run_id,
                seq=0,
                area_id=area.id,
                passed=all(result.passed for result in results),
            )
        )
        return tuple(results)
