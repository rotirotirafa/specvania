"""Offline checks of the pixel kit's diffable data and gallery contract.

These checks cannot establish artistic originality or visual sharpness. G6 still
requires the reviewer to inspect sprites.html in a browser.
"""

import base64
import hashlib
import json
import re
import struct
from html.parser import HTMLParser
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/specvania/ui/static"
PARTY = ("mage", "warrior", "paladin")
SPRITE_NAMES = (
    *PARTY,
    "boss",
    "monster",
    "door",
    *(f"room_a{number}" for number in range(1, 11)),
    "heart",
    "coin",
    "stamina_pip",
)
type Frames = dict[str, list[list[str]]]
type Sprites = dict[str, Frames]


def definition(filename: str, name: str, wrapper: str) -> object:
    """Read the JSON literal exported by an ES module without executing JS."""
    source = (STATIC / "pixel" / filename).read_text()
    match = re.search(
        rf"export const {name} = {re.escape(wrapper)}\((\{{.*?\}})\);",
        source,
        re.DOTALL,
    )
    assert match is not None, f"{name} must remain a statically readable literal"
    return json.loads(match[1])


@pytest.fixture
def sprites() -> Sprites:
    return cast(Sprites, definition("sprites.js", "SPRITES", "freezeSprites"))


@pytest.fixture
def palette() -> dict[str, str | None]:
    return cast(
        dict[str, str | None], definition("palette.js", "PALETTE", "Object.freeze")
    )


def test_sprite_inventory_matches_room_and_world_areas(sprites: Sprites) -> None:
    assert set(sprites) == set(SPRITE_NAMES)
    areas = re.findall(r"^## (A\d+) ·", (ROOT / "docs/tasks.md").read_text(), re.M)
    assert {f"room_{area.lower()}" for area in areas} == {
        name for name in sprites if name.startswith("room_")
    }


@pytest.mark.parametrize("name", PARTY)
def test_party_has_idle_walk_act_rest_frames(sprites: Sprites, name: str) -> None:
    assert set(sprites[name]) == {"idle", "walk", "act", "rest"}
    # Motion states need distinct poses, not repeated copies of an idle frame.
    for state in ("walk", "act"):
        assert len({tuple(frame) for frame in sprites[name][state]}) >= 2
    assert (
        len({tuple(frame) for frames in sprites[name].values() for frame in frames}) > 4
    )


@pytest.mark.parametrize("name", SPRITE_NAMES)
def test_no_state_has_identical_consecutive_frames(sprites: Sprites, name: str) -> None:
    for state, frames in sprites[name].items():
        for index, (previous, current) in enumerate(
            zip(frames, frames[1:], strict=False), start=1
        ):
            assert previous != current, (
                f"{name}.{state}: frames {index} and {index + 1} are identical"
            )


@pytest.mark.parametrize("name", ("boss", "monster"))
def test_boss_and_monster_have_distinct_idle_and_defeated_frames(
    sprites: Sprites, name: str
) -> None:
    assert set(sprites[name]) == {"idle", "defeated"}
    assert sprites[name]["idle"] != sprites[name]["defeated"]


def test_door_has_distinct_locked_and_open_frames(sprites: Sprites) -> None:
    assert set(sprites["door"]) == {"locked", "open"}
    assert sprites["door"]["locked"] != sprites["door"]["open"]


@pytest.mark.parametrize("name", SPRITE_NAMES)
def test_every_frame_is_16_by_16_and_uses_only_palette_pixels(
    sprites: Sprites, palette: dict[str, str | None], name: str
) -> None:
    assert sprites[name]
    for frames in sprites[name].values():
        assert frames
        for frame in frames:
            assert len(frame) == 16
            assert all(len(row) == 16 for row in frame)
            pixels = set("".join(frame))
            assert pixels <= palette.keys()
            assert pixels - {"."}, "A frame cannot be entirely transparent"


def test_palette_has_transparency_and_valid_unique_colors(
    palette: dict[str, str | None],
) -> None:
    assert palette["."] is None
    colors = [color for key, color in palette.items() if key != "."]
    assert all(len(key) == 1 for key in palette)
    assert all(
        isinstance(color, str) and re.fullmatch(r"#[0-9a-f]{6}", color)
        for color in colors
    )
    assert len(set(colors)) == len(colors)


def test_room_tiles_use_their_area_color(sprites: Sprites) -> None:
    areas = cast(
        dict[str, str], definition("palette.js", "AREA_COLORS", "Object.freeze")
    )
    assert set(areas) == {f"A{number}" for number in range(1, 11)}
    assert len(set(areas.values())) == 10
    for area, color in areas.items():
        for frame in sprites[f"room_{area.lower()}"]["idle"]:
            assert color in "".join(frame)
            assert set("".join(frame)) <= {".", "K", "S", color}


class Gallery(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.feed(source)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))


def test_gallery_includes_every_sprite_and_every_frame(sprites: Sprites) -> None:
    source = (STATIC / "sprites.html").read_text()
    gallery = Gallery(source)
    names = [
        attrs["data-sprite"] for _, attrs in gallery.elements if "data-sprite" in attrs
    ]
    assert len(names) == len(sprites)
    assert set(names) == set(sprites)
    # No frame filter or hand-picked subset: render each state and each frame.
    assert "Object.entries(SPRITES[name])" in source
    assert "frames.forEach((frame, index)" in source
    assert "textureKey(name, state, index)" in source
    assert "appendChild(card)" in source
    assert "registerTextures(this)" in source


def test_gallery_has_exact_integer_scales_and_nearest_neighbor_settings() -> None:
    source = (STATIC / "sprites.html").read_text()
    canvases = [attrs for tag, attrs in Gallery(source).elements if tag == "canvas"]
    assert {
        (attrs.get("data-scale"), attrs.get("width"), attrs.get("height"))
        for attrs in canvases
    } == {("1", "16", "16"), ("2", "32", "32"), ("4", "64", "64")}
    for setting in (
        "pixelArt: true",
        "roundPixels: true",
        "width: 320",
        "height: 180",
        "imageSmoothingEnabled = false",
        "image-rendering: pixelated",
    ):
        assert setting in source
    assert "canvas.width, canvas.height" in source


def test_texture_loader_registers_all_frames_without_binary_art() -> None:
    source = (STATIC / "pixel/textures.js").read_text()
    for setting in (
        'from "./palette.js"',
        'from "./sprites.js"',
        "Object.entries(SPRITES)",
        "Object.entries(states)",
        "frames.forEach",
        "scene.textures.createCanvas(key, 16, 16)",
        "context.fillRect(x, y, 1, 1)",
        "texture.refresh()",
        "Phaser.Textures.FilterMode.NEAREST",
        "scene.textures.exists(key)",
    ):
        assert setting in source
    assert "PALETTE[pixel]" in source
    assert "imageSmoothingEnabled = false" in source
    for path in (*sorted((STATIC / "pixel").glob("*.js")), STATIC / "sprites.html"):
        text = path.read_text()
        assert not re.search(
            r"\.(?:png|gif|jpe?g|webp|svg)\b|data:image|https?://", text
        )
        if path.suffix == ".js":
            assert (
                not re.search(r"#[0-9a-fA-F]{3,8}\b", text) or path.name == "palette.js"
            )
    assert 'src="./vendor/phaser.min.js"' in (STATIC / "sprites.html").read_text()


def test_sprite_data_is_deeply_frozen() -> None:
    source = (STATIC / "pixel/sprites.js").read_text()
    assert "Object.freeze(frame)" in source
    assert "Object.freeze(frames)" in source
    assert "Object.freeze(states)" in source
    assert "return Object.freeze(sprites)" in source


def test_gallery_uses_local_modules_font_and_palette_colors_only() -> None:
    source = (STATIC / "sprites.html").read_text()
    assert not re.search(r"https?://|//[a-zA-Z]|data:image", source)
    for tag, attrs in Gallery(source).elements:
        for attribute in ("src", "href"):
            if attribute in attrs:
                url = attrs[attribute]
                assert url is not None and url.startswith("./")
                assert (STATIC / url).is_file(), f"Missing local {tag} resource: {url}"
    imports = re.findall(r'from "(\./[^\"]+)"', source)
    assert len(imports) == 3
    assert all((STATIC / path).is_file() for path in imports)
    assert "document.documentElement.style.setProperty" in source
    assert "Object.entries(UI_COLORS)" in source
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", source)
    assert "backgroundColor: UI_COLORS.background" in source


def test_gallery_has_no_motion_or_live_loop() -> None:
    source = (STATIC / "sprites.html").read_text()
    # Reviewing poses never starts animations, timers, tweens or an update loop;
    # thus the default and prefers-reduced-motion views both remain static.
    assert not re.search(
        r"\b(?:setTimeout|setInterval|requestAnimationFrame|update)\s*\(|"
        r"\b(?:tweens|anims)\.|animation\s*:",
        source,
    )


def test_vendored_phaser_matches_pinned_version_digest_and_mit_notice() -> None:
    binary = (STATIC / "vendor/phaser.min.js").read_bytes()
    assert hashlib.sha256(binary).hexdigest() == (
        "e92ddef111ba42e92d316979c732311757093688ea1810591cb7aa2858eba7a7"
    )
    sri = "sha256-" + base64.b64encode(hashlib.sha256(binary).digest()).decode()
    gallery = (STATIC / "sprites.html").read_text()
    assert f'integrity="{sri}"' in gallery
    assert 'data-version="3.90.0"' in gallery
    assert 'Phaser.VERSION !== "3.90.0"' in gallery
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    for text in (
        "Phaser",
        "3.90.0",
        "https://registry.npmjs.org/phaser/-/phaser-3.90.0.tgz",
        "MIT",
        "Copyright (c) 2024 Richard Davey, Phaser Studio Inc.",
        "Permission is hereby granted",
        "THE SOFTWARE IS PROVIDED",
    ):
        assert text in notices


def test_vendored_font_version_digest_license_and_local_use() -> None:
    binary = (STATIC / "vendor/fonts/PressStart2P-Regular.ttf").read_bytes()
    assert hashlib.sha256(binary).hexdigest() == (
        "034c77f1f05ec89421e4a63f0e3a4ca1ecf852cc6d2bf611f126f275728e017d"
    )
    # Verify the font's own version metadata, rather than invent a Google Fonts release.
    count = struct.unpack_from(">H", binary, 4)[0]
    versions: list[str] = []
    for index in range(count):
        tag, _, offset, _ = struct.unpack_from(">4sIII", binary, 12 + 16 * index)
        if tag == b"name":
            _, records, strings = struct.unpack_from(">HHH", binary, offset)
            for record in range(records):
                platform, _, _, name, length, start = struct.unpack_from(
                    ">6H", binary, offset + 6 + record * 12
                )
                if platform == 3 and name == 5:
                    versions.append(
                        binary[
                            offset + strings + start : offset + strings + start + length
                        ].decode("utf-16-be")
                    )
    assert "Version 3.000" in versions
    license_text = (STATIC / "vendor/fonts/OFL.txt").read_text()
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    for text in (
        "Press Start 2P",
        "3.000",
        "SIL Open Font License",
        "1.1",
        "https://github.com/google/fonts/tree/main/ofl/pressstart2p",
        "Copyright 2012 The Press Start 2P Project Authors",
        "cody@zone38.net",
    ):
        assert text in notices
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert (
        "./vendor/fonts/PressStart2P-Regular.ttf"
        in (STATIC / "sprites.html").read_text()
    )
