"""World topology is additive and ready for the engine's world_loaded event."""

from pathlib import Path

from starlette.testclient import TestClient

from specvania.clock import FakeClock
from specvania.run.events import (
    EVENT_ADAPTER,
    Event,
    RedactionGuard,
    Respawn,
    RoomCleared,
    RoomEntered,
    RoomFailed,
    RoomInfo,
    RoomSkipped,
    WorldLoaded,
    build_world_payload,
)
from specvania.run.jsonl import encode_events
from specvania.run.state import fold_events
from specvania.sdd.world import parse_world
from specvania.ui.server import UIServer, load_replay

ROOT = Path(__file__).resolve().parents[1]


def test_repository_world_payload_contains_areas_rooms_doors_and_boss_ids() -> None:
    world = parse_world(ROOT / "docs")
    original = world.model_dump_json()
    payload = build_world_payload(world)
    assert len(payload.rooms) == 47
    assert len(payload.areas) == 12
    assert [(area.id, area.title, area.boss_ids) for area in payload.areas] == [
        (area.id, area.title, area.boss) for area in world.areas
    ]
    assert {(door.src, door.dst) for door in payload.doors} == {
        (door.src, door.dst) for door in world.doors
    }
    assert payload.boss_ids == tuple(boss for area in world.areas for boss in area.boss)
    assert len(set(payload.boss_ids)) == 27
    for room, parsed in zip(payload.rooms, world.rooms, strict=True):
        area = next(area for area in world.areas if area.id == parsed.area_id)
        assert (room.id, room.title, room.area, room.depends_on) == (
            parsed.id,
            parsed.title,
            parsed.area_id,
            parsed.depends_on,
        )
        assert (room.area_title, room.boss_ids) == (area.title, area.boss)
        assert room.state == ("locked" if parsed.depends_on else "open")
    assert build_world_payload(world) == payload
    assert world.model_dump_json() == original
    event = WorldLoaded(
        ts=FakeClock().now(), run_id="map", seq=1, **payload.model_dump()
    )
    assert EVENT_ADAPTER.validate_json(event.model_dump_json()) == event


def test_legacy_world_loaded_still_decodes_and_snapshot_keeps_topology() -> None:
    old = WorldLoaded(
        ts=FakeClock().now(),
        run_id="old",
        seq=1,
        rooms=(RoomInfo(id="T01", area="A1", title="Room"),),
    )
    raw = old.model_dump(mode="json")
    for field in ("areas", "doors", "boss_ids"):
        raw.pop(field, None)
    for field in ("state", "area_title", "boss_ids"):
        raw["rooms"][0].pop(field, None)
    decoded = EVENT_ADAPTER.validate_python(raw)
    assert isinstance(decoded, WorldLoaded)
    assert decoded.rooms[0].id == "T01"
    assert decoded.areas == decoded.doors == decoded.boss_ids == ()

    payload = build_world_payload(parse_world(ROOT / "docs"))
    state = fold_events(
        [WorldLoaded(ts=FakeClock().now(), run_id="map", seq=1, **payload.model_dump())]
    )
    assert len(state.rooms) == 47
    assert {room.area for room in state.rooms} == {area.id for area in payload.areas}
    assert {boss for room in state.rooms for boss in room.boss_ids} == set(
        payload.boss_ids
    )
    assert {(src, room.id) for room in state.rooms for src in room.depends_on} == {
        (door.src, door.dst) for door in payload.doors
    }


def map_preview_events() -> tuple[Event, ...]:
    """Synthetic T16 review fixture; two-second gaps make transitions visible."""
    clock = FakeClock()
    payload = build_world_payload(parse_world(ROOT / "docs"))
    events: list[Event] = [
        WorldLoaded(ts=clock.now(), run_id="t16-preview", seq=1, **payload.model_dump())
    ]
    changes = (
        ("room_entered", "T01", {}),
        ("room_failed", "T01", {"reason": "Synthetic failure"}),
        ("respawn", "T01", {"attempt": 2, "feedback": "Synthetic respawn"}),
        ("room_cleared", "T01", {"commit": "synthetic"}),
        ("room_entered", "T02", {}),
        ("room_skipped", "T02", {"reason": "Synthetic skip"}),
        ("room_entered", "T03", {}),
        ("room_cleared", "T03", {"commit": "synthetic"}),
    )
    for seq, (event_type, room_id, fields) in enumerate(changes, 2):
        clock.sleep(2)
        events.append(
            EVENT_ADAPTER.validate_python(
                dict(
                    type=event_type,
                    room_id=room_id,
                    seq=seq,
                    run_id="t16-preview",
                    ts=clock.now(),
                    **fields,
                )
            )
        )
    return tuple(events)


def test_manual_preview_jsonl_replays_world_and_every_room_state(
    tmp_path: Path,
) -> None:
    # Exercise the exact JSONL + UIServer path used for the reviewer's G6 check.
    guard = RedactionGuard()
    events = map_preview_events()
    path = tmp_path / "t16-preview.jsonl"
    path.write_text(encode_events(events, guard))
    loaded = load_replay(path, guard=guard)
    assert loaded == events
    states = {
        room.state.value
        for count in range(1, len(events) + 1)
        for room in fold_events(events[:count]).rooms
    }
    assert states == {"locked", "open", "active", "cleared", "failed", "skipped"}
    assert any(isinstance(event, RoomEntered) for event in loaded)
    assert any(isinstance(event, RoomFailed) for event in loaded)
    assert any(isinstance(event, Respawn) for event in loaded)
    assert any(isinstance(event, RoomCleared) for event in loaded)
    assert any(isinstance(event, RoomSkipped) for event in loaded)
    server = UIServer(
        guard=guard,
        clock=FakeClock(),
        run_id="t16-preview",
        replay=loaded,
        port=8765,
    )
    with TestClient(server.app, base_url="http://127.0.0.1:8765") as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
            headers={"Origin": "http://127.0.0.1:8765"},
        ) as websocket:
            assert websocket.receive_json()["kind"] == "state"
            for event in loaded:
                assert websocket.receive_json() == {
                    "kind": "event",
                    "event": event.model_dump(mode="json"),
                }
