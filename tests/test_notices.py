"""T38: every shipped UI asset/runtime has a named license notice."""

import re
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("heading", "license_name"),
    [
        ("Kenney Tiny Dungeon", "CC0 1.0"),
        ("Monaco editor", "MIT"),
        ("Phaser", "MIT"),
        ("Press Start 2P", "SIL Open Font License, Version 1.1"),
        ("Electron desktop runtime", "MIT"),
    ],
)
def test_notices_name_asset_license_and_license_source(
    heading: str, license_name: str
) -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "THIRD_PARTY_NOTICES.md").read_text()
    section = text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    assert f"**{license_name}" in section
    assert "https://" in section
    for relative in re.findall(r"`(src/[^`]+)`", section):
        assert (root / relative).exists(), relative
    if heading == "Electron desktop runtime":
        assert "LICENSES.chromium.html" in section
