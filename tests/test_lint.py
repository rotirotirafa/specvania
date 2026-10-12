"""L1–L6 evidence for room T06, including the installed command."""

import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specvania.cli import create_app
from specvania.sdd.lint import WorldLintError, lint_directory, validate_directory


def fixture_world(name: str = "valid") -> Path:
    return Path(__file__).parent / "fixtures" / "worlds" / name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("valid", []),
        ("l1_duplicate", [("spec.md", 8, "L1")]),
        ("l2_unknown_door", [("tasks.md", 5, "L2")]),
        ("l2_cycle", [("tasks.md", 13, "L2")]),
        ("l3_missing_files", [("tasks.md", 4, "L3")]),
        ("l4_uncovered", [("spec.md", 1, "L4"), ("spec.md", 1, "L4")]),
        ("l5_late_coverage", [("tasks.md", 2, "L5")]),
    ],
)
def test_fixtures_produce_exact_rule_ids_and_lines(
    name: str, expected: list[tuple[str, int, str]]
) -> None:
    errors = lint_directory(fixture_world(name))
    assert [(e.path.name, e.line, e.rule) for e in errors] == expected


def test_cycles_report_full_path() -> None:
    errors = lint_directory(fixture_world("l2_cycle"))
    assert len(errors) == 1
    assert errors[0].message == "Door cycle: T01 → T02 → T01"


def test_three_room_cycle_reports_every_door_in_its_direction(tmp_path: Path) -> None:
    rooms = (fixture_world("l2_cycle") / "tasks.md").read_text()
    rooms = rooms.replace("depends_on: T01", "depends_on: T03")
    rooms += (
        "\n### T03 · Complete the cycle\ndepends_on: T01\ncovers: none\n"
        "files: other.py\n\nChecklist:\n- [ ] Complete the cycle\n"
    )
    write_world(tmp_path, rooms=rooms)
    assert [e.message for e in lint_directory(tmp_path)] == [
        "Door cycle: T01 → T03 → T02 → T01"
    ]


@pytest.mark.parametrize(
    ("filename", "before", "after", "line"),
    [
        ("spec.md", "### S1 · Accept a world", "### S1 Accept a world", 1),
        ("spec.md", "### S1", "## S1", 1),
        ("spec.md", "### S1", "#### S1", 1),
        ("tasks.md", "## A1 · Spellbook", "## A1 Spellbook", 1),
        ("tasks.md", "### T01 · Parse a world", "### T01 Parse a world", 4),
        ("tasks.md", "### T01", "#### T01", 4),
    ],
)
def test_malformed_declarations_are_not_silently_dropped(
    tmp_path: Path, filename: str, before: str, after: str, line: int
) -> None:
    write_world(tmp_path)
    path = tmp_path / filename
    path.write_text(path.read_text().replace(before, after))
    assert any(
        e.rule == "L1" and e.path == path and e.line == line
        for e in lint_directory(tmp_path)
    )


def write_world(
    tmp_path: Path, spec: str | None = None, rooms: str | None = None
) -> None:
    (tmp_path / "spec.md").write_text(
        spec if spec is not None else (fixture_world() / "spec.md").read_text()
    )
    (tmp_path / "tasks.md").write_text(
        rooms if rooms is not None else (fixture_world() / "tasks.md").read_text()
    )


@pytest.mark.parametrize(
    ("before", "after", "rule", "line"),
    [
        ("## A1 ·", "## Ax ·", "L1", 1),
        ("### T01 ·", "### T1 ·", "L1", 4),
        ("depends_on: none", "depends_on: T1", "L1", 5),
        ("covers: S1", "covers: Sx", "L1", 6),
        ("boss: S1", "boss: Sx", "L1", 2),
        ("boss: S1", "boss: S99", "L4", 2),
        ("covers: S1", "covers: S99", "L4", 6),
        ("depends_on: none\n", "", "L3", 4),
        ("covers: S1\n", "", "L3", 4),
        ("files: world.py", "files:", "L3", 7),
        ("files: world.py", "files: world.py # comment", "L3", 7),
        ("covers: S1", "covers: S1 # comment", "L3", 6),
        ("depends_on: none", "depends_on: none, T01", "L3", 5),
        ("depends_on: none", "depends_on:none", "L3", 5),
        ("files: world.py", "files: world.py,", "L3", 7),
        ("files: world.py", "files: world.py\nfiles: lint.py", "L3", 8),
        ("Checklist:", "Checklist: # comment", "L3", 4),
        ("- [ ] Parse a world", "", "L3", 9),
    ],
)
def test_invalid_ids_required_fields_and_exact_metadata(
    tmp_path: Path, before: str, after: str, rule: str, line: int
) -> None:
    rooms = (fixture_world() / "tasks.md").read_text().replace(before, after)
    write_world(tmp_path, rooms=rooms)
    assert (line, rule) in [(e.line, e.rule) for e in lint_directory(tmp_path)]


@pytest.mark.parametrize("entity", ["area", "room", "scenario"])
def test_duplicate_ids_are_rejected(tmp_path: Path, entity: str) -> None:
    spec = (fixture_world() / "spec.md").read_text()
    rooms = (fixture_world() / "tasks.md").read_text()
    if entity == "scenario":
        spec += "\n" + spec
    elif entity == "room":
        rooms += "\n" + rooms[rooms.index("###") :]
    else:
        rooms += "\n## A1 · Duplicate\nboss: none\n"
    write_world(tmp_path, spec, rooms)
    assert any(
        e.rule == "L1" and f"Duplicate {entity} id" in e.message
        for e in lint_directory(tmp_path)
    )


def test_invalid_scenario_id_is_rejected(tmp_path: Path) -> None:
    spec = (fixture_world() / "spec.md").read_text().replace("S1 ·", "Sx ·")
    write_world(tmp_path, spec=spec)
    assert any(e.rule == "L1" and e.line == 1 for e in lint_directory(tmp_path))


def test_scenario_requires_gherkin(tmp_path: Path) -> None:
    write_world(tmp_path, spec="### S1 · Missing acceptance\nPlain text\n")
    assert any(e.rule == "L1" and e.line == 1 for e in lint_directory(tmp_path))


def test_later_area_door_is_rejected(tmp_path: Path) -> None:
    rooms = (fixture_world("l5_late_coverage") / "tasks.md").read_text()
    rooms = rooms.replace("depends_on: none", "depends_on: T02")
    write_world(tmp_path, rooms=rooms)
    assert any(
        e.rule == "L2" and e.line == 5 and "later area" in e.message
        for e in lint_directory(tmp_path)
    )


@pytest.mark.parametrize("boss", ["S1", "S1, S1"])
def test_scenario_must_be_boss_exactly_once(tmp_path: Path, boss: str) -> None:
    rooms = (fixture_world() / "tasks.md").read_text()
    if boss == "S1":
        rooms += "\n## A2 · Second boss\nboss: S1\n"
    else:
        rooms = rooms.replace("boss: S1", f"boss: {boss}")
    write_world(tmp_path, rooms=rooms)
    assert any(
        e.rule == "L4"
        and e.path.name == "spec.md"
        and e.line == 1
        and "found 2" in e.message
        for e in lint_directory(tmp_path)
    )


def test_earlier_area_coverage_and_forward_same_area_doors_are_valid(
    tmp_path: Path,
) -> None:
    rooms = (fixture_world("l5_late_coverage") / "tasks.md").read_text()
    rooms = rooms.replace("boss: S1", "boss: none").replace(
        "boss: none\n\n### T02", "boss: S1\n\n### T02"
    )
    rooms = rooms.replace("covers: none", "covers: S1").replace(
        "covers: S1\nfiles: other.py", "covers: none\nfiles: other.py"
    )
    write_world(tmp_path, rooms=rooms)
    assert lint_directory(tmp_path) == ()
    rooms = rooms.replace("## A2 · Dungeon\nboss: S1\n\n", "")
    rooms = rooms.replace("boss: none", "boss: S1").replace(
        "depends_on: T01", "depends_on: none"
    )
    rooms = rooms.replace("depends_on: none", "depends_on: T02", 1)
    write_world(tmp_path, rooms=rooms)
    assert lint_directory(tmp_path) == ()


def test_self_door_cycle_reports_full_path(tmp_path: Path) -> None:
    rooms = (
        (fixture_world() / "tasks.md")
        .read_text()
        .replace("depends_on: none", "depends_on: T01")
    )
    write_world(tmp_path, rooms=rooms)
    assert [e.message for e in lint_directory(tmp_path)] == ["Door cycle: T01 → T01"]


def test_cli_reports_one_line_per_error_with_nonzero_exit() -> None:
    directory = fixture_world("l2_cycle")
    result = CliRunner().invoke(create_app(), ["lint", str(directory)])
    assert result.exit_code == 1
    assert (
        result.stdout == f"{directory / 'tasks.md'}:13 L2 Door cycle: T01 → T02 → T01\n"
    )
    assert "Traceback" not in result.output


def test_cli_missing_world_reports_typed_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(create_app(), ["lint", str(tmp_path)])
    assert result.exit_code == 1
    assert result.stdout.startswith(f"{tmp_path / 'spec.md'}:1 L6 Cannot read world:")
    assert "Traceback" not in result.output


def test_cli_requires_world_directory() -> None:
    result = CliRunner().invoke(create_app(), ["lint"])
    assert result.exit_code == 2
    assert "Missing argument" in result.output


def test_cli_valid_world_prints_ok() -> None:
    result = CliRunner().invoke(create_app(), ["lint", str(fixture_world())])
    assert result.exit_code == 0
    assert result.stdout == "OK\n"


def test_validation_raises_typed_error_with_every_diagnostic() -> None:
    directory = fixture_world("l4_uncovered")
    with pytest.raises(WorldLintError) as caught:
        validate_directory(directory)
    assert caught.value.diagnostics == lint_directory(directory)
    assert len(caught.value.diagnostics) == 2
    assert [r.id for r in validate_directory(fixture_world()).rooms] == ["T01"]


def test_repository_lint_prints_ok() -> None:
    root = Path(__file__).parents[1]
    result = subprocess.run(
        ["specvania", "lint", "docs/"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "OK\n"
    assert result.stderr == ""
    assert os.environ["NVIDIA_API_KEY"] not in result.stdout + result.stderr
