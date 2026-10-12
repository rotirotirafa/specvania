"""World format v0 rules L1–L6; terminal rendering belongs to the CLI."""

import re
from pathlib import Path

from specvania.sdd.world import Diagnostic, Source, World, parse_world


class WorldLintError(Exception):
    """A readable world has user-fixable format, door or coverage violations."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__(f"World has {len(diagnostics)} lint errors")


def lint_world(world: World) -> tuple[Diagnostic, ...]:
    """Return every detected violation, with its original file and line."""
    errors = list(world.diagnostics)

    def error(source: Source, rule: str, message: str, line: int | None = None) -> None:
        errors.append(
            Diagnostic(
                path=source.path, line=line or source.line, rule=rule, message=message
            )
        )

    for entities, pattern, kind in (
        (world.scenarios, r"S[0-9]+", "Scenario"),
        (world.areas, r"A[0-9]+", "Area"),
        (world.rooms, r"T[0-9]{2}", "Room"),
    ):
        seen: set[str] = set()
        for entity in entities:
            if not re.fullmatch(pattern, entity.id):
                error(entity, "L1", f"Invalid {kind.lower()} id {entity.id}")
            if entity.id in seen:
                error(entity, "L1", f"Duplicate {kind.lower()} id {entity.id}")
            seen.add(entity.id)

    areas = {area.id: index for index, area in enumerate(world.areas)}
    rooms = {room.id: room for room in world.rooms}
    scenarios = {scenario.id for scenario in world.scenarios}
    for room in world.rooms:
        for key, ids, line, pattern in (
            ("depends_on", room.depends_on, room.depends_on_line, r"T[0-9]{2}"),
            ("covers", room.covers, room.covers_line, r"S[0-9]+"),
        ):
            for identifier in ids:
                if not re.fullmatch(pattern, identifier):
                    error(room, "L1", f"Invalid {key} id {identifier}", line)
        for identifier in room.depends_on:
            source = rooms.get(identifier)
            if source is None:
                error(
                    room,
                    "L2",
                    f"Unknown door source {identifier}",
                    room.depends_on_line,
                )
            elif areas.get(source.area_id, -1) > areas.get(room.area_id, -1):
                error(
                    room,
                    "L2",
                    f"Door {identifier} → {room.id} comes from a later area",
                    room.depends_on_line,
                )
        for identifier in room.covers:
            if identifier not in scenarios:
                error(
                    room,
                    "L4",
                    f"Unknown covered scenario {identifier}",
                    room.covers_line,
                )

    # Iterative DFS keeps large worlds independent of Python's recursion limit.
    visited: set[str] = set()
    for root in rooms:
        if root in visited:
            continue
        path = [root]
        active = {root: 0}
        visited.add(root)
        stack = [iter(rooms[root].depends_on)]
        while stack:
            identifier = next(stack[-1], None)
            if identifier is None:
                stack.pop()
                active.pop(path.pop())
            elif identifier in active:
                # DFS follows prerequisites; reverse to display src → dst doors.
                cycle = list(reversed([*path[active[identifier] :], identifier]))
                room = rooms[path[-1]]
                error(
                    room, "L2", "Door cycle: " + " → ".join(cycle), room.depends_on_line
                )
            elif identifier in rooms and identifier not in visited:
                visited.add(identifier)
                active[identifier] = len(path)
                path.append(identifier)
                stack.append(iter(rooms[identifier].depends_on))

    for area in world.areas:
        for identifier in area.boss:
            if not re.fullmatch(r"S[0-9]+", identifier):
                error(area, "L1", f"Invalid boss id {identifier}", area.boss_line)
            if identifier not in scenarios:
                error(area, "L4", f"Unknown boss scenario {identifier}", area.boss_line)
            elif not any(
                identifier in room.covers
                and areas.get(room.area_id, -1) <= areas[area.id]
                for room in world.rooms
            ):
                error(
                    area,
                    "L5",
                    f"Boss {identifier} needs coverage in its own or an earlier area",
                    area.boss_line,
                )
    for scenario in world.scenarios:
        if not any(scenario.id in room.covers for room in world.rooms):
            error(scenario, "L4", f"Scenario {scenario.id} is not covered by any room")
        count = sum(area.boss.count(scenario.id) for area in world.areas)
        if count != 1:
            error(
                scenario,
                "L4",
                f"Scenario {scenario.id} must be boss of exactly one area "
                f"(found {count})",
            )
    return tuple(errors)


def lint_directory(directory: Path) -> tuple[Diagnostic, ...]:
    """Load and validate a hand-written world."""
    return lint_world(parse_world(directory))


def validate_directory(directory: Path) -> World:
    """Load a valid world or raise a typed error for the caller to render."""
    world = parse_world(directory)
    diagnostics = lint_world(world)
    if diagnostics:
        raise WorldLintError(diagnostics)
    return world
