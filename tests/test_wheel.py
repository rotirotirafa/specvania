"""T38 package resources: configuration always; artifact after CI's uv build."""

import json
import tomllib
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/specvania/ui/static"


def resources() -> list[Path]:
    files = [STATIC / "transport.js"] + [
        path
        for folder in ("i18n", "vendor/kenney-tiny-dungeon", "vendor/monaco")
        for path in (STATIC / folder).rglob("*")
        if path.is_file()
    ]
    # An empty/missing vendored directory must not turn this into a vacuous test.
    for required in (
        "transport.js",
        "i18n/en.json",
        "i18n/pt-BR.json",
        "i18n/index.js",
        "vendor/kenney-tiny-dungeon/tilemap_packed.png",
        "vendor/kenney-tiny-dungeon/License.txt",
        "vendor/monaco/LICENSE",
        "vendor/monaco/ThirdPartyNotices.txt",
        "vendor/monaco/worker.js",
        "vendor/monaco/vs/loader.js",
        "vendor/monaco/vs/editor/editor.main.js",
        "vendor/monaco/vs/editor/editor.main.css",
        "vendor/monaco/vs/editor/codicon.ttf",
    ):
        assert STATIC / required in files, required
    return files


def test_wheel_configuration_includes_i18n_kenney_and_monaco() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["build-system"]["build-backend"] == "uv_build"
    build = config.get("tool", {}).get("uv", {}).get("build-backend", {})
    assert build.get("module-root", "src") == "src"
    assert build.get("module-name", "specvania") == "specvania"
    assert not build.get("source-exclude")
    assert not build.get("wheel-exclude")
    # uv_build includes non-Python package data under its module root by default.
    assert all(path.stat().st_size for path in resources())


def test_built_wheel_contains_i18n_kenney_and_monaco() -> None:
    files = resources()
    wheels = sorted((ROOT / "dist").glob("specvania-*.whl"))
    if not wheels:
        pytest.skip("No wheel present; CI/reviewer runs uv build before this test")
    assert len(wheels) == 1, "Inspect a clean dist directory with exactly one wheel"
    with ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        for path in files:
            name = path.relative_to(ROOT / "src").as_posix()
            assert name in names, name
            assert wheel.read(name) == path.read_bytes(), name


def test_ci_inspects_built_wheel_and_runs_desktop_check() -> None:
    jobs = json.loads((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    assert {"checks", "postgres", "postgres-e2e", "wheel"} <= jobs.keys()
    commands = [step["run"] for step in jobs["wheel"]["steps"] if "run" in step]
    inspection = "uv run pytest -q tests/test_e2e.py tests/test_wheel.py -k built_wheel"
    assert commands.index("uv build") < commands.index(inspection)
    desktop = jobs["desktop-check"]["steps"]
    assert any(
        step.get("uses", "").startswith("actions/setup-node@") for step in desktop
    )
    commands = [step["run"] for step in desktop if "run" in step]
    assert commands.index("uv sync --locked") < commands.index(
        "npm --prefix apps/desktop run check"
    )
