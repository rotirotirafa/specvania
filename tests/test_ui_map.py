"""Execute the real vanilla ES modules with Node built-ins, no network or npm."""

import shutil
import subprocess
from pathlib import Path

from specvania.run.events import build_world_payload
from specvania.sdd.world import parse_world

ROOT = Path(__file__).resolve().parents[1]


def run_map_check(check: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required to prove the T16 JavaScript layout"
    payload = build_world_payload(parse_world(ROOT / "docs"))
    result = subprocess.run(
        [
            node,
            str(ROOT / "tests/ui/world-map.mjs"),
            check,
        ],
        cwd=ROOT,
        input=payload.model_dump_json(),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_repository_world_layout_has_no_overlapping_rooms() -> None:
    run_map_check("layout")


def test_room_states_have_distinct_geometry_without_color() -> None:
    run_map_check("looks")


def test_live_and_replay_project_every_room_state_and_doors() -> None:
    run_map_check("projection")


def test_map_mount_snapshot_camera_overview_integer_zoom_and_reduced_motion() -> None:
    run_map_check("mount")


def test_index_mounts_map_before_transport_and_uses_local_pixel_assets() -> None:
    source = (ROOT / "src/specvania/ui/static/index.html").read_text()
    assert source.index("mountMap(Phaser,") < source.index('await import("./main.js")')
    assert 'src="./vendor/phaser.min.js"' in source
    assert 'url("./vendor/fonts/PressStart2P-Regular.ttf")' in source
    assert "image-rendering: pixelated" in source
    assert 'id="world-map"' in source
    assert "whole map / follow party" in source
