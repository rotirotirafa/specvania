"""Parse world format v0 without interpreting examples inside Markdown fences."""

import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Source(BaseModel):
    """A one-based location in a world document."""

    model_config = ConfigDict(frozen=True)
    path: Path
    line: int


class Diagnostic(Source):
    rule: str
    message: str


class WorldParseError(Exception):
    """A world document cannot be read; callers can render its diagnostic."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


class Scenario(Source):
    id: str
    title: str
    gherkin: str


class ChecklistItem(Source):
    text: str
    checked: bool


class Room(Source):
    id: str
    title: str
    area_id: str
    depends_on: tuple[str, ...] = ()
    covers: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    checklist: tuple[ChecklistItem, ...] = ()
    context: str = ""
    depends_on_line: int = 0
    covers_line: int = 0
    files_line: int = 0


class Area(Source):
    id: str
    title: str
    boss: tuple[str, ...] = ()
    boss_line: int = 0


class Door(Source):
    src: str
    dst: str


class World(BaseModel):
    model_config = ConfigDict(frozen=True)
    scenarios: tuple[Scenario, ...]
    areas: tuple[Area, ...]
    rooms: tuple[Room, ...]
    doors: tuple[Door, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


def visible_lines(lines: list[str]) -> Iterator[tuple[int, str]]:
    """Yield structural lines outside backtick or tilde Markdown fences."""
    marker = ""
    length = 0
    for number, line in enumerate(lines, 1):
        fence = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if marker:
            if (
                fence
                and fence[1][0] == marker
                and len(fence[1]) >= length
                and not fence[2].strip()
            ):
                marker = ""
            continue
        if fence and (fence[1][0] != "`" or "`" not in fence[2]):
            marker, length = fence[1][0], len(fence[1])
            continue
        yield number, line


def _heading(line: str) -> tuple[int, str] | None:
    match = re.fullmatch(r" {0,3}(#{1,6})\s+(.+?)\s*", line)
    return (len(match[1]), match[2]) if match else None


def _identity(title: str, prefix: str) -> tuple[str, str] | None:
    # Keep malformed ids so L1 can report them instead of silently dropping them.
    match = re.fullmatch(rf"({prefix}\S*)\s+·\s+(.+)", title)
    return (match[1], match[2]) if match else None


def _read(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise WorldParseError(
            Diagnostic(
                path=path, line=1, rule="L6", message=f"Cannot read world: {error}"
            )
        ) from error


def _gherkin(lines: list[str]) -> str:
    marker = ""
    length = 0
    start = 0
    is_gherkin = False
    for index, line in enumerate(lines):
        fence = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if marker:
            if (
                fence
                and fence[1][0] == marker
                and len(fence[1]) >= length
                and not fence[2].strip()
            ):
                if is_gherkin:
                    return "\n".join(lines[start:index])
                marker = ""
        elif fence and (fence[1][0] != "`" or "`" not in fence[2]):
            marker, length, start = fence[1][0], len(fence[1]), index + 1
            is_gherkin = fence[2].strip() == "gherkin"
    return ""


def parse_world(directory: Path) -> World:
    """Read both documents; preserve invalid structures for rule diagnostics."""
    spec_path, rooms_path = directory / "spec.md", directory / "tasks.md"
    spec_lines, rooms_lines = _read(spec_path), _read(rooms_path)
    diagnostics: list[Diagnostic] = []

    def error(path: Path, line: int, rule: str, message: str) -> None:
        diagnostics.append(Diagnostic(path=path, line=line, rule=rule, message=message))

    for path, lines, declarations in (
        (spec_path, spec_lines, (("S", 3),)),
        (rooms_path, rooms_lines, (("A", 2), ("T", 3))),
    ):
        for number, line in visible_lines(lines):
            if not (heading := _heading(line)):
                continue
            level, title = heading
            for prefix, expected_level in declarations:
                identity = _identity(title, prefix)
                if (identity or re.match(rf"{prefix}[0-9]", title)) and (
                    not identity or level != expected_level
                ):
                    error(
                        path,
                        number,
                        "L1",
                        f"Expected {'#' * expected_level} {prefix}<id> · Title",
                    )

    spec_headings = [
        (number, heading)
        for number, line in visible_lines(spec_lines)
        if (heading := _heading(line)) and heading[0] <= 3
    ]
    scenarios: list[Scenario] = []
    for index, (number, (level, title)) in enumerate(spec_headings):
        identity = _identity(title, "S")
        if level != 3 or not identity:
            continue
        end = (
            spec_headings[index + 1][0] - 1 if index + 1 < len(spec_headings) else None
        )
        gherkin = _gherkin(spec_lines[number:end])
        if not gherkin.strip():
            error(
                spec_path, number, "L1", "Scenario requires a non-empty gherkin fence"
            )
        scenarios.append(
            Scenario(
                path=spec_path,
                line=number,
                id=identity[0],
                title=identity[1],
                gherkin=gherkin,
            )
        )

    structural = list(visible_lines(rooms_lines))
    headings = [
        (index, number, heading)
        for index, (number, line) in enumerate(structural)
        if (heading := _heading(line)) and heading[0] <= 3
    ]

    def metadata(
        body: list[tuple[int, str]], key: str, heading_line: int, rule: str
    ) -> tuple[tuple[str, ...], int]:
        matches = [
            (n, line[len(key) + 1 :]) for n, line in body if line.startswith(key + ":")
        ]
        if not matches:
            error(rooms_path, heading_line, rule, f"Missing {key}:")
            return (), heading_line
        if len(matches) > 1:
            error(rooms_path, matches[1][0], rule, f"Repeated {key}:")
        number, raw = matches[0]
        if raw == " none" and key != "files":
            return (), number
        values = tuple(value.strip() for value in raw.split(","))
        if (
            not raw.startswith(" ")
            or any(not value for value in values)
            or "#" in raw
            or ("none" in values and key != "files")
        ):
            error(rooms_path, number, rule, f"Invalid {key}: list; no inline comments")
        return values, number

    areas: list[Area] = []
    rooms: list[Room] = []
    area_id = ""
    for index, (offset, number, (level, title)) in enumerate(headings):
        end_offset = (
            headings[index + 1][0] if index + 1 < len(headings) else len(structural)
        )
        body = structural[offset + 1 : end_offset]
        if level <= 2:
            area_id = ""
        if level == 2 and (identity := _identity(title, "A")):
            area_id = identity[0]
            boss, boss_line = metadata(body, "boss", number, "L4")
            areas.append(
                Area(
                    path=rooms_path,
                    line=number,
                    id=area_id,
                    title=identity[1],
                    boss=boss,
                    boss_line=boss_line,
                )
            )
        elif level == 3 and (identity := _identity(title, "T")):
            if not area_id:
                error(rooms_path, number, "L3", "Room must belong to an area")
            depends_on, depends_on_line = metadata(body, "depends_on", number, "L3")
            covers, covers_line = metadata(body, "covers", number, "L3")
            files, files_line = metadata(body, "files", number, "L3")
            checklist: list[ChecklistItem] = []
            checklist_lines = [n for n, line in body if line == "Checklist:"]
            if len(checklist_lines) > 1:
                error(rooms_path, checklist_lines[1], "L3", "Repeated Checklist:")
            if checklist_lines:
                for n, line in body:
                    match = re.fullmatch(r"- \[([ xX])\] (\S.*)", line)
                    if n > checklist_lines[0] and match:
                        checklist.append(
                            ChecklistItem(
                                path=rooms_path,
                                line=n,
                                text=match[2],
                                checked=match[1].lower() == "x",
                            )
                        )
            if not checklist:
                error(
                    rooms_path,
                    checklist_lines[0] if checklist_lines else number,
                    "L3",
                    "Room requires a non-empty Checklist:",
                )
            end_line = headings[index + 1][1] - 1 if index + 1 < len(headings) else None
            rooms.append(
                Room(
                    path=rooms_path,
                    line=number,
                    id=identity[0],
                    title=identity[1],
                    area_id=area_id,
                    depends_on=depends_on,
                    covers=covers,
                    files=files,
                    checklist=tuple(checklist),
                    context="\n".join(rooms_lines[number:end_line]),
                    depends_on_line=depends_on_line,
                    covers_line=covers_line,
                    files_line=files_line,
                )
            )
    doors = tuple(
        Door(path=room.path, line=room.depends_on_line, src=source, dst=room.id)
        for room in rooms
        for source in room.depends_on
    )
    return World(
        scenarios=tuple(scenarios),
        areas=tuple(areas),
        rooms=tuple(rooms),
        doors=doors,
        diagnostics=tuple(diagnostics),
    )
