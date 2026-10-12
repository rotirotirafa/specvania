"""Source-aware parsing and Markdown fence behavior for T06."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from specvania.sdd.world import World, WorldParseError, parse_world


def fixture_world(name: str = "valid") -> Path:
    return Path(__file__).parent / "fixtures" / "worlds" / name


def test_parse_typed_world_with_source_lines_and_checklist() -> None:
    world = parse_world(fixture_world("l2_cycle"))
    assert isinstance(world, World)
    assert [(s.id, s.title, s.line) for s in world.scenarios] == [
        ("S1", "Accept a world", 1)
    ]
    assert world.scenarios[0].gherkin == (
        "Given a world\nWhen it is linted\nThen it passes"
    )
    area = world.areas[0]
    assert (area.id, area.title, area.line, area.boss_line, area.boss) == (
        "A1",
        "Spellbook",
        1,
        2,
        ("S1",),
    )
    room = world.rooms[0]
    assert (room.id, room.area_id, room.line, room.depends_on_line) == (
        "T01",
        "A1",
        4,
        5,
    )
    assert (room.covers_line, room.files_line, room.covers, room.files) == (
        6,
        7,
        ("S1",),
        ("world.py",),
    )
    assert room.path == fixture_world("l2_cycle") / "tasks.md"
    assert "Checklist:" in room.context
    assert [(d.src, d.dst, d.line, d.path) for d in world.doors] == [
        ("T02", "T01", 5, room.path),
        ("T01", "T02", 13, room.path),
    ]
    assert [
        (r.checklist[0].text, r.checklist[0].checked, r.checklist[0].line)
        for r in world.rooms
    ] == [("Parse a world", False, 10), ("Lint a world", True, 18)]
    assert World.model_validate_json(world.model_dump_json()) == world
    with pytest.raises(ValidationError):
        room.title = "Changed"


@pytest.mark.parametrize("fence", ["```", "~~~~", "````", "   ~~~"])
def test_headings_and_metadata_inside_fences_are_ignored(
    tmp_path: Path, fence: str
) -> None:
    fake = "\n".join(
        [
            "### S99 · Fake scenario",
            "## A99 · Fake area",
            "boss: S99",
            "### T99 · Fake room",
            "depends_on: T99",
            "covers: S99",
            "files: fake.py",
            "Checklist:",
            "- [x] Fake checklist",
        ]
    )
    for filename in ("spec.md", "tasks.md"):
        original = (fixture_world() / filename).read_text()
        # A shorter or opposite fence does not close the outer fence.
        inner = "~~~" if "`" in fence else "```"
        prefix = f"{fence}markdown\n{inner}\n{fake}\n{fence}\n"
        (tmp_path / filename).write_text(prefix + original)
    world = parse_world(tmp_path)
    assert [s.id for s in world.scenarios] == ["S1"]
    assert [a.id for a in world.areas] == ["A1"]
    assert [r.id for r in world.rooms] == ["T01"]
    assert world.rooms[0].depends_on == ()
    assert len(world.rooms[0].checklist) == 1
    assert world.scenarios[0].line == 13
    assert world.rooms[0].line == 16
    assert world.diagnostics == ()


def test_short_fence_and_suffix_do_not_close_longer_fence(tmp_path: Path) -> None:
    fake = "````markdown\n```\n### S99 · Fake\n````suffix\n### S98 · Fake\n`````\n"
    (tmp_path / "spec.md").write_text(fake + (fixture_world() / "spec.md").read_text())
    (tmp_path / "tasks.md").write_text((fixture_world() / "tasks.md").read_text())
    world = parse_world(tmp_path)
    assert [s.id for s in world.scenarios] == ["S1"]
    assert world.scenarios[0].line == 7


def test_fenced_metadata_does_not_satisfy_required_room_fields(tmp_path: Path) -> None:
    (tmp_path / "spec.md").write_text((fixture_world() / "spec.md").read_text())
    rooms = (fixture_world() / "tasks.md").read_text()
    rooms = rooms.replace("files: world.py", "```text\nfiles: world.py\n```")
    rooms = rooms.replace("- [ ] Parse a world", "~~~text\n- [ ] Parse a world\n~~~")
    (tmp_path / "tasks.md").write_text(rooms)
    world = parse_world(tmp_path)
    assert world.rooms[0].files == ()
    assert world.rooms[0].checklist == ()
    assert [(e.line, e.rule) for e in world.diagnostics] == [(4, "L3"), (11, "L3")]


def test_nested_gherkin_example_is_not_scenario_acceptance(tmp_path: Path) -> None:
    (tmp_path / "spec.md").write_text(
        "### S1 · Missing acceptance\n````markdown\n```gherkin\n"
        "Given only an example\n```\n````\n"
    )
    (tmp_path / "tasks.md").write_text((fixture_world() / "tasks.md").read_text())
    world = parse_world(tmp_path)
    assert world.scenarios[0].gherkin == ""
    assert [(e.line, e.rule) for e in world.diagnostics] == [(1, "L1")]


@pytest.mark.parametrize("filename", ["spec.md", "tasks.md"])
def test_unreadable_world_raises_typed_error(tmp_path: Path, filename: str) -> None:
    for name in ("spec.md", "tasks.md"):
        if name != filename:
            (tmp_path / name).write_text("")
    with pytest.raises(WorldParseError) as caught:
        parse_world(tmp_path)
    assert caught.value.diagnostic.path == tmp_path / filename
    assert caught.value.diagnostic.line == 1
    assert caught.value.diagnostic.rule == "L6"


def test_invalid_utf8_raises_typed_error(tmp_path: Path) -> None:
    (tmp_path / "spec.md").write_bytes(b"\xff")
    with pytest.raises(WorldParseError) as caught:
        parse_world(tmp_path)
    assert caught.value.diagnostic.rule == "L6"


def test_repository_world_has_exact_entities_despite_format_examples() -> None:
    world = parse_world(Path(__file__).parents[1] / "docs")
    assert [s.id for s in world.scenarios] == [f"S{i}" for i in range(1, 18)]
    assert [a.id for a in world.areas] == [f"A{i}" for i in range(1, 7)]
    assert [r.id for r in world.rooms] == [f"T{i:02}" for i in range(1, 24)]
    assert ("T01", "T06") in {(d.src, d.dst) for d in world.doors}
