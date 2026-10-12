"""Deterministic projections from immutable events, with real state assertions."""

import pytest

from specvania.run.events import EVENT_ADAPTER, Event
from specvania.run.state import RoomState, StateFoldError, fold_events


def stream(*payloads: dict[str, object]) -> tuple[Event, ...]:
    return tuple(
        EVENT_ADAPTER.validate_python(
            {"ts": "2026-10-12T12:00:00Z", "run_id": "demo", "seq": seq, **payload}
        )
        for seq, payload in enumerate(payloads, 1)
    )


def lifecycle() -> tuple[Event, ...]:
    return stream(
        {"type": "run_started", "feature": "demo-feature", "branch": "specvania/demo"},
        {
            "type": "world_loaded",
            "rooms": [
                {"id": "T01", "area": "A1", "title": "One", "depends_on": []},
                {"id": "T02", "area": "A1", "title": "Two", "depends_on": ["T01"]},
                {"id": "T03", "area": "A1", "title": "Three", "depends_on": ["T02"]},
            ],
        },
        {"type": "room_entered", "room_id": "T01"},
        {"type": "attempt_started", "room_id": "T01", "attempt": 1},
        {
            "type": "model_call",
            "role": "warrior",
            "model": "fake/one",
            "tokens_in": 10,
            "tokens_out": 5,
            "latency": 2,
            "status": 429,
        },
        {
            "type": "cooldown",
            "role": "warrior",
            "model": "fake/one",
            "seconds": 5,
            "reason": "limited",
        },
        {
            "type": "fallback",
            "role": "warrior",
            "from_model": "fake/one",
            "to_model": "fake/two",
            "reason": "unavailable",
        },
        {
            "type": "model_call",
            "role": "warrior",
            "model": "fake/two",
            "tokens_in": 7,
            "tokens_out": 3,
            "latency": 1.5,
            "status": 200,
        },
        {"type": "room_failed", "room_id": "T01", "reason": "review failed"},
        {"type": "respawn", "room_id": "T01", "attempt": 2, "feedback": "Add tests"},
        {"type": "room_cleared", "room_id": "T01", "commit": "abc"},
        {"type": "room_skipped", "room_id": "T02", "reason": "player skipped"},
        {"type": "boss_started", "area_id": "A1", "scenarios": ["S1", "S2"]},
        {
            "type": "boss_scenario",
            "area_id": "A1",
            "scenario": "S1",
            "passed": True,
            "reason": "Verified",
        },
        {
            "type": "boss_scenario",
            "area_id": "A1",
            "scenario": "S2",
            "passed": False,
            "reason": "Missing",
        },
        {"type": "boss_result", "area_id": "A1", "passed": False},
        {
            "type": "bug_opened",
            "bug_id": "bug1",
            "title": "Missing",
            "body": "Evidence missing",
            "feature": "demo-feature",
            "room_id": "T02",
            "source": "room_skipped",
        },
        {
            "type": "bug_closed",
            "bug_id": "bug1",
            "status": "fixed",
            "fixed_by_run": "demo",
        },
        {"type": "run_paused", "reason": "failed boss", "room_id": None},
    )


def test_folding_same_event_list_twice_produces_identical_state() -> None:
    events = lifecycle()
    before = tuple(event.model_dump_json() for event in events)
    first, second = fold_events(events), fold_events(iter(events))
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert tuple(event.model_dump_json() for event in events) == before
    assert first.run_id == "demo"
    assert first.seq == 19
    assert first.status == "paused"
    assert first.reason == "failed boss"
    assert first.feature == "demo-feature"
    assert first.branch == "specvania/demo"
    assert [
        (room.id, room.state, room.attempt, room.commit) for room in first.rooms
    ] == [
        ("T01", RoomState.CLEARED, 2, "abc"),
        ("T02", RoomState.SKIPPED, 0, None),
        ("T03", RoomState.LOCKED, 0, None),
    ]
    assert [
        (
            model.model,
            model.calls,
            model.tokens_in,
            model.tokens_out,
            model.latency,
            model.rate_limits,
        )
        for model in first.models
    ] == [
        ("fake/one", 1, 10, 5, 2, 1),
        ("fake/two", 1, 7, 3, 1.5, 0),
    ]
    assert first.party[0].model == "fake/two"
    assert first.bosses[0].passed is False
    assert [(result.scenario, result.passed) for result in first.bosses[0].results] == [
        ("S1", True),
        ("S2", False),
    ]
    assert first.bugs[0].status == "fixed"
    assert first.bugs[0].fixed_by_run == "demo"


def test_projection_tracks_doors_active_failed_and_respawn_states() -> None:
    events = lifecycle()
    assert [room.state for room in fold_events(events[:2]).rooms] == [
        RoomState.OPEN,
        RoomState.LOCKED,
        RoomState.LOCKED,
    ]
    assert fold_events(events[:4]).rooms[0].state == RoomState.ACTIVE
    assert fold_events(events[:9]).rooms[0].state == RoomState.FAILED
    assert fold_events(events[:10]).rooms[0].state == RoomState.ACTIVE
    assert fold_events(events[:11]).rooms[1].state == RoomState.OPEN
    assert fold_events(events[:6]).party[0].status == "cooldown"
    assert fold_events(events[:6]).party[0].cooldown_seconds == 5
    assert fold_events(events[:17]).bugs[0].status == "open"


@pytest.mark.parametrize("status", ["finished", "aborted"])
def test_finished_status_is_projected_and_folds_are_isolated(status: str) -> None:
    state = fold_events(
        (
            *lifecycle(),
            EVENT_ADAPTER.validate_python(
                {
                    "type": "run_finished",
                    "ts": "2026-10-12T12:00:01Z",
                    "run_id": "demo",
                    "seq": 20,
                    "status": status,
                }
            ),
        )
    )
    assert state.status == status
    assert state.reason is None
    empty = fold_events(())
    assert empty.run_id is None
    assert empty.seq == 0
    assert empty.rooms == empty.models == empty.bugs == ()


@pytest.mark.parametrize("mutation", [{"run_id": "other"}, {"seq": 1}, {"seq": 0}])
def test_fold_refuses_mixed_runs_or_non_increasing_seq(
    mutation: dict[str, object],
) -> None:
    first = lifecycle()[0]
    invalid = EVENT_ADAPTER.validate_python(
        {**first.model_dump(mode="json"), **mutation}
    )
    with pytest.raises(StateFoldError):
        fold_events((first, invalid))
