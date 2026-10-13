"""T17 exercises vanilla modules with Node and real recorded event snapshots."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from specvania.clock import FakeClock
from specvania.run.events import EVENT_ADAPTER, RedactionGuard, RunStarted
from specvania.run.jsonl import decode_events
from specvania.run.state import fold_events

ROOT = Path(__file__).resolve().parents[1]


def run_hud_check(check: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for T17 UI checks"
    events = decode_events(
        (ROOT / "src/specvania/examples/demo/events.jsonl").read_text(),
        RedactionGuard(),
    )
    result = subprocess.run(
        [node, str(ROOT / "tests/ui/hud.mjs"), check],
        cwd=ROOT,
        input=json.dumps(
            {
                "events": [event.model_dump(mode="json") for event in events],
                "snapshots": [
                    fold_events(events[:count]).model_dump(mode="json")
                    for count in (13, 25, 43, 44)
                ],
            }
        ),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_event_status_mapping_table_and_cooldown_countdown() -> None:
    run_hud_check("statuses")


def test_fallback_updates_visible_model_and_stamina() -> None:
    run_hud_check("fallback")


def test_hearts_reset_and_priced_coins_or_unpriced_tokens() -> None:
    run_hud_check("metrics")


def test_latest_dialogue_uses_safe_pixel_text_and_palette() -> None:
    run_hud_check("dialogue")


def test_boss_hp_loses_one_segment_per_unique_passed_scenario() -> None:
    run_hud_check("boss")


def test_bug_monsters_bestiary_defeat_and_snapshot_restore() -> None:
    run_hud_check("bugs")


def test_read_only_approval_hint_is_absent_with_control_token() -> None:
    run_hud_check("approval")


def test_reduced_motion_disables_walk_respawn_and_defeat() -> None:
    run_hud_check("motion")


def test_map_and_hud_mount_on_real_transport_events() -> None:
    run_hud_check("integration")


def test_run_started_optional_hud_fields_round_trip_and_fold() -> None:
    legacy = RunStarted(
        ts=FakeClock().now(), run_id="hud", seq=1, feature="hud", branch="test"
    )
    assert legacy.lives is None
    assert legacy.prices == ()
    raw = legacy.model_dump(mode="json")
    raw.update(
        lives=5,
        prices=[
            {
                "model": "test/warrior",
                "input_per_mtok": "2.5",
                "output_per_mtok": "5",
                "currency": "USD",
            }
        ],
    )
    event = EVENT_ADAPTER.validate_python(raw)
    assert isinstance(event, RunStarted)
    assert EVENT_ADAPTER.validate_json(event.model_dump_json()) == event
    state = fold_events([event])
    assert state.lives == 5
    assert state.prices == event.prices
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python({**raw, "lives": 0})
    for rate in (-1, "NaN", "Infinity"):
        price = {**raw["prices"][0], "input_per_mtok": rate}
        with pytest.raises(ValidationError):
            EVENT_ADAPTER.validate_python({**raw, "prices": [price]})


def test_snapshot_keeps_latest_dialogue_status_and_failed_lives() -> None:
    payloads = [
        {"type": "run_started", "feature": "hud", "branch": "test", "lives": 2},
        {"type": "room_entered", "room_id": "T01"},
        {"type": "attempt_started", "room_id": "T01", "attempt": 1},
        {
            "type": "approval_requested",
            "request_id": "r1",
            "command": "test",
            "cwd": "/repo",
        },
        {
            "type": "approval_answered",
            "request_id": "r1",
            "answer": True,
            "responder": "terminal",
        },
        {
            "type": "review",
            "room_id": "T01",
            "verdict": "fail",
            "checklist": [],
            "feedback": "Need evidence",
        },
        {"type": "room_failed", "room_id": "T01", "reason": "Need evidence"},
    ]
    events = [
        EVENT_ADAPTER.validate_python(
            {"ts": FakeClock().now(), "run_id": "hud", "seq": seq, **payload}
        )
        for seq, payload in enumerate(payloads, 1)
    ]
    state = fold_events(events[:4])
    assert (
        next(member for member in state.party if member.role == "warrior").status
        == "waiting for player"
    )
    assert state.dialogue == events[3]
    state = fold_events(events[:5])
    assert state.dialogue == events[4]
    state = fold_events(events)
    assert state.party_room == "T01"
    assert state.rooms[0].failed_attempts == 1
    assert state.dialogue == events[5]


def test_snapshot_cooldown_preserves_duration_and_remaining_time() -> None:
    from datetime import timedelta

    clock = FakeClock()
    events = [
        EVENT_ADAPTER.validate_python(
            dict(
                ts=clock.now(),
                run_id="hud",
                seq=1,
                type="cooldown",
                role="warrior",
                model="w1",
                seconds=10,
                reason="backoff",
            )
        ),
        EVENT_ADAPTER.validate_python(
            dict(
                ts=clock.now() + timedelta(seconds=5),
                run_id="hud",
                seq=2,
                type="bug_opened",
                bug_id="bug",
                title="Bug",
                body="",
                source="user",
            )
        ),
    ]
    member = fold_events(events).party[0]
    assert member.cooldown_seconds == 5
    assert member.cooldown_duration == 10
    assert member.status == "cooldown"
