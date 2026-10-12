import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from specvania.agent.reviewer import Reviewer, ReviewError
from specvania.agent.sandbox import Sandbox
from specvania.clock import FakeClock
from specvania.config import Config, LimitsConfig
from specvania.provider.client import Completion, ProviderError
from specvania.provider.fake import ScriptedProvider
from specvania.provider.router import Router
from specvania.run.events import BossResult, BossScenario, Event, GateResult, Review
from specvania.sdd.world import Area, ChecklistItem, Room, Scenario


def room() -> Room:
    return Room(
        path=Path("tasks.md"),
        line=4,
        id="T10",
        title="Review",
        area_id="A1",
        files=("allowed.py",),
        context="Implement the acceptance checks.",
        checklist=(
            ChecklistItem(
                path=Path("tasks.md"), line=10, text="Behavior works", checked=False
            ),
        ),
    )


def answer(*, verdict: str = "pass", ok: bool = True) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "checklist": [
                {"item": "Behavior works", "ok": ok, "note": "Verified the diff"}
            ],
            "feedback": "Acceptance checked",
        }
    )


def setup(
    root: Path, output: str | ProviderError | Sequence[str | ProviderError]
) -> tuple[Reviewer, ScriptedProvider, list[Event], FakeClock]:
    clock = FakeClock()
    events: list[Event] = []
    script = [output] if isinstance(output, str | ProviderError) else output
    provider = ScriptedProvider(
        [
            Completion(content=value) if isinstance(value, str) else value
            for value in script
        ]
    )
    router = Router(
        config=Config(),
        provider=provider,
        clock=clock,
        run_id="run",
        emit=events.append,
        jitter=lambda: 0,
    )
    return (
        Reviewer(
            router=router,
            sandbox=Sandbox(root),
            clock=clock,
            run_id="run",
            emit=events.append,
        ),
        provider,
        events,
        clock,
    )


def gate(clock: FakeClock, code: int = 0) -> GateResult:
    return GateResult(
        ts=clock.now(),
        run_id="run",
        seq=0,
        room_id="T10",
        command="pytest -q",
        exit_code=code,
        output_summary="short result",
    )


def test_failing_gate_overrides_model_pass(tmp_path: Path) -> None:
    reviewer, _, events, clock = setup(tmp_path, answer())
    result = reviewer.review_room(room(), attempt_diff="", gates=(gate(clock, 1),))
    assert result.verdict == "fail"
    assert next(e for e in events if isinstance(e, Review)).verdict == "fail"


def test_not_ok_checklist_overrides_model_pass(tmp_path: Path) -> None:
    reviewer, _, events, _ = setup(tmp_path, answer(ok=False))
    result = reviewer.review_room(room(), attempt_diff="", gates=())
    assert result.verdict == "fail"
    assert next(e for e in events if isinstance(e, Review)).checklist[0].ok is False


def test_review_emits_one_complete_event(tmp_path: Path) -> None:
    reviewer, provider, events, clock = setup(tmp_path, answer())
    reviewer.review_room(
        room(),
        attempt_diff="diff --git a/allowed.py b/allowed.py\n+changed line\n",
        gates=(gate(clock),),
    )
    reviews = [e for e in events if isinstance(e, Review)]
    assert len(reviews) == 1
    event = reviews[0]
    assert (event.run_id, event.seq, event.ts, event.room_id) == (
        "run",
        0,
        clock.now(),
        "T10",
    )
    assert event.verdict == "pass"
    assert [(i.item, i.ok, i.note) for i in event.checklist] == [
        ("Behavior works", True, "Verified the diff")
    ]
    assert event.feedback == "Acceptance checked"
    assert provider.calls[0].model == Config().roles.paladin[0]
    payload = json.loads(provider.calls[0].messages[1].content)
    assert payload["room"]["context"] == "Implement the acceptance checks."
    assert payload["room"]["checklist"] == ["Behavior works"]
    assert payload["gate_results"][0]["exit_code"] == 0
    assert "+changed line" in payload["diff"]


def test_model_fail_is_preserved_with_all_checks_ok(tmp_path: Path) -> None:
    reviewer, _, _, _ = setup(tmp_path, answer(verdict="fail"))
    assert reviewer.review_room(room(), attempt_diff="", gates=()).verdict == "fail"


@pytest.mark.parametrize(
    "output",
    [
        "not JSON",
        answer().replace("true", '"true"'),
        '{"verdict":"pass","checklist":[],"feedback":"ok"}',
        answer().replace("Behavior works", "Different item"),
        json.dumps(
            {
                "verdict": "pass",
                "checklist": [{"item": "Behavior works", "ok": True, "note": ""}] * 2,
                "feedback": "",
            }
        ),
    ],
)
def test_invalid_incomplete_or_rephrased_review_is_handled_safely(
    tmp_path: Path, output: str
) -> None:
    reviewer, _, events, _ = setup(tmp_path, [output] * 3)
    if output == "not JSON" or '"true"' in output:
        with pytest.raises(ReviewError):
            reviewer.review_room(room(), attempt_diff="", gates=())
        assert not any(isinstance(e, Review) for e in events)
    else:
        result = reviewer.review_room(room(), attempt_diff="", gates=())
        assert len(result.checklist) == 1
        assert result.checklist[0].item == "Behavior works"
        if not json.loads(output)["checklist"]:
            assert result.verdict == "fail"
            assert not result.checklist[0].ok
            assert result.checklist[0].note == "not reviewed by the Paladin"
        else:
            assert result.verdict == "pass" and result.checklist[0].ok
        assert len([e for e in events if isinstance(e, Review)]) == 1


@pytest.mark.parametrize("passed", [True, False])
def test_boss_emits_each_scenario_and_aggregate(tmp_path: Path, passed: bool) -> None:
    output = json.dumps(
        [
            {"scenario": "S2", "passed": passed, "reason": "Second check"},
            {"scenario": "S1", "passed": True, "reason": "First check"},
        ]
    )
    reviewer, provider, events, clock = setup(tmp_path, output)
    area = Area(
        path=Path("tasks.md"), line=1, id="A1", title="Dungeon", boss=("S1", "S2")
    )
    scenarios = tuple(
        Scenario(
            path=Path("spec.md"),
            line=1,
            id=id,
            title=f"Check {id}",
            gherkin=f"Given {id}\nThen works",
        )
        for id in ("S1", "S2")
    )
    result = reviewer.evaluate_boss(
        area,
        scenarios=scenarios,
        area_diff="diff --git a/allowed.py b/allowed.py\n+area change",
        gates=(gate(clock),),
    )
    boss_events = [e for e in events if isinstance(e, BossScenario | BossResult)]
    assert [e.type for e in boss_events] == [
        "boss_scenario",
        "boss_scenario",
        "boss_result",
    ]
    checks = [e for e in boss_events if isinstance(e, BossScenario)]
    assert [(e.scenario, e.passed, e.reason) for e in checks] == [
        ("S1", True, "First check"),
        ("S2", passed, "Second check"),
    ]
    assert all(e.area_id == "A1" and e.ts == clock.now() for e in checks)
    assert isinstance(boss_events[-1], BossResult) and boss_events[-1].passed is passed
    assert result == tuple(checks)
    payload = json.loads(provider.calls[0].messages[1].content)
    assert [s["gherkin"] for s in payload["scenarios"]] == [
        "Given S1\nThen works",
        "Given S2\nThen works",
    ]
    assert "+area change" in payload["diff"]
    assert payload["gate_results"][0]["command"] == "pytest -q"


@pytest.mark.parametrize(
    "output",
    [
        "[]",
        '[{"scenario":"S99","passed":true,"reason":"ok"}]',
        '[{"scenario":"S1","passed":true,"reason":"ok"},'
        '{"scenario":"S1","passed":true,"reason":"ok"}]',
    ],
)
def test_boss_missing_unknown_or_duplicate_scenarios_emit_no_partial_results(
    tmp_path: Path, output: str
) -> None:
    reviewer, _, events, _ = setup(tmp_path, [output] * 3)
    area = Area(path=Path("tasks.md"), line=1, id="A1", title="Dungeon", boss=("S1",))
    scenario = Scenario(
        path=Path("spec.md"), line=1, id="S1", title="Check", gherkin="Then works"
    )
    with pytest.raises(ReviewError):
        reviewer.evaluate_boss(area, scenarios=(scenario,), area_diff="", gates=())
    assert not any(isinstance(e, BossScenario | BossResult) for e in events)


def test_paladin_context_contains_only_room_diff_and_gate_summaries(
    tmp_path: Path,
) -> None:
    (tmp_path / "allowed.py").write_text("UNSUPPLIED_ALLOWED_CONTENT")
    (tmp_path / "unrelated.py").write_text("UNRELATED_PRIVATE_CONTENT")
    reviewer, provider, _, _ = setup(tmp_path, answer())
    reviewer.review_room(
        room(),
        attempt_diff="diff --git a/allowed.py b/allowed.py\n+DIFF_EVIDENCE",
        gates=(),
    )
    sent = "\n".join(m.content for m in provider.calls[0].messages)
    assert "DIFF_EVIDENCE" in sent
    assert "UNRELATED_PRIVATE_CONTENT" not in sent
    assert "UNSUPPLIED_ALLOWED_CONTENT" not in sent
    assert "unrelated.py" not in sent


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "private.pem",
        "id_rsa",
        ".git/config",
        "../outside.py",
        "ignored.py",
        "alias.py",
    ],
)
def test_protected_diff_content_never_reaches_paladin(
    tmp_path: Path, path: str
) -> None:
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)], check=True, capture_output=True
    )
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "alias.py").symlink_to(tmp_path.parent / "outside.py")
    reviewer, provider, _, _ = setup(tmp_path, answer())
    reviewer.review_room(
        room(),
        attempt_diff=f"diff --git a/{path} b/{path}\n+SECRET_DIFF_CONTENT",
        gates=(),
    )
    assert "SECRET_DIFF_CONTENT" not in "\n".join(
        m.content for m in provider.calls[0].messages
    )


def test_long_review_text_is_summarized_in_events(tmp_path: Path) -> None:
    output = json.loads(answer())
    output["feedback"] = "x" * 1000
    output["checklist"][0]["note"] = "y" * 1000
    reviewer, _, events, _ = setup(tmp_path, json.dumps(output))
    result = reviewer.review_room(room(), attempt_diff="", gates=())
    assert len(result.feedback) == 1000
    event = next(e for e in events if isinstance(e, Review))
    assert len(event.feedback) <= 200 and event.feedback.endswith("[truncated]")
    assert len(event.checklist[0].note) <= 200


def test_provider_error_propagates_without_success_event(tmp_path: Path) -> None:
    reviewer, _, events, _ = setup(tmp_path, ProviderError(status=401))
    with pytest.raises(ProviderError):
        reviewer.review_room(room(), attempt_diff="", gates=())
    assert not any(isinstance(e, Review) for e in events)


def test_safe_diff_preserves_allowed_sections_and_checks_both_rename_paths(
    tmp_path: Path,
) -> None:
    reviewer, provider, _, _ = setup(tmp_path, answer())
    diff = (
        "diff --git a/allowed.py b/allowed.py\n+FIRST_ALLOWED\n"
        "diff --git a/.env b/renamed.py\n+SECRET_OLD_NAME\n"
        "diff --git a/renamed.py b/private.key\n+SECRET_NEW_NAME\n"
        'diff --git "a/file name.py" "b/file name.py"\n+SECOND_ALLOWED\n'
        "diff --git a/another file.py b/another file.py\n+THIRD_ALLOWED\n"
    )
    reviewer.review_room(room(), attempt_diff=diff, gates=())
    sent = json.loads(provider.calls[0].messages[1].content)["diff"]
    assert (
        "FIRST_ALLOWED" in sent and "SECOND_ALLOWED" in sent and "THIRD_ALLOWED" in sent
    )
    assert "SECRET_OLD_NAME" not in sent and "SECRET_NEW_NAME" not in sent


@pytest.mark.parametrize(
    "diff",
    [
        "+UNSTRUCTURED_CONTENT",
        "diff --git invalid\n+CONTENT",
        'diff --git "unterminated\n+CONTENT',
    ],
)
def test_unstructured_diff_is_rejected_before_model_call(
    tmp_path: Path, diff: str
) -> None:
    reviewer, provider, events, _ = setup(tmp_path, answer())
    with pytest.raises(ReviewError):
        reviewer.review_room(room(), attempt_diff=diff, gates=())
    assert provider.calls == [] and events == []


def test_boss_context_excludes_unsupplied_files_and_protected_diff(
    tmp_path: Path,
) -> None:
    (tmp_path / "unrelated.py").write_text("UNRELATED_FILE_CONTENT")
    reviewer, provider, _, _ = setup(
        tmp_path, '[{"scenario":"S1","passed":true,"reason":"Evidence checked"}]'
    )
    area = Area(path=Path("tasks.md"), line=1, id="A1", title="Dungeon", boss=("S1",))
    scenario = Scenario(
        path=Path("spec.md"), line=1, id="S1", title="Check", gherkin="Then works"
    )
    reviewer.evaluate_boss(
        area,
        scenarios=(scenario,),
        area_diff=(
            "diff --git a/.env b/.env\n+SECRET_CONTENT\n"
            "diff --git a/allowed.py b/allowed.py\n+AREA_EVIDENCE\n"
        ),
        gates=(),
    )
    sent = "\n".join(m.content for m in provider.calls[0].messages)
    assert "AREA_EVIDENCE" in sent
    assert "UNRELATED_FILE_CONTENT" not in sent and "SECRET_CONTENT" not in sent


def test_missing_boss_input_scenario_is_rejected_before_model_call(
    tmp_path: Path,
) -> None:
    reviewer, provider, events, _ = setup(tmp_path, "[]")
    area = Area(path=Path("tasks.md"), line=1, id="A1", title="Dungeon", boss=("S1",))
    with pytest.raises(ReviewError):
        reviewer.evaluate_boss(area, scenarios=(), area_diff="", gates=())
    assert provider.calls == [] and events == []


@pytest.mark.parametrize(
    "wrapper", ["```json\n{}\n```", "The verdict follows.\n{}\nEnd of review."]
)
def test_room_extracts_json_from_fences_and_prose(tmp_path: Path, wrapper: str) -> None:
    reviewer, _, events, _ = setup(tmp_path, wrapper.format(answer()))
    assert reviewer.review_room(room(), attempt_diff="", gates=()).verdict == "pass"
    assert len([e for e in events if isinstance(e, Review)]) == 1


def test_boss_extracts_fenced_json_array(tmp_path: Path) -> None:
    reviewer, _, events, _ = setup(
        tmp_path, '```json\n[{"scenario":"S1","passed":true,"reason":"Verified"}]\n```'
    )
    area = Area(path=Path("tasks.md"), line=1, id="A1", title="Dungeon", boss=("S1",))
    scenario = Scenario(
        path=Path("spec.md"), line=1, id="S1", title="Check", gherkin="Then works"
    )
    assert reviewer.evaluate_boss(area, scenarios=(scenario,), area_diff="", gates=())[
        0
    ].passed
    assert len([e for e in events if isinstance(e, BossResult)]) == 1


@pytest.mark.parametrize("boss", [False, True])
def test_two_invalid_outputs_are_repaired_with_validation_errors(
    tmp_path: Path, boss: bool
) -> None:
    valid = (
        '[{"scenario":"S1","passed":true,"reason":"Verified"}]' if boss else answer()
    )
    invalid = (
        '[{"scenario":"S1","passed":"yes","reason":"ok"}]'
        if boss
        else answer().replace("true", '"yes"')
    )
    reviewer, provider, events, _ = setup(tmp_path, ["not JSON", invalid, valid])
    if boss:
        area = Area(
            path=Path("tasks.md"), line=1, id="A1", title="Dungeon", boss=("S1",)
        )
        scenario = Scenario(
            path=Path("spec.md"), line=1, id="S1", title="Check", gherkin="Then works"
        )
        reviewer.evaluate_boss(area, scenarios=(scenario,), area_diff="", gates=())
        assert len([e for e in events if isinstance(e, BossResult)]) == 1
    else:
        assert reviewer.review_room(room(), attempt_diff="", gates=()).verdict == "pass"
        assert len([e for e in events if isinstance(e, Review)]) == 1
    assert len(provider.calls) == 3
    first_repair = json.loads(provider.calls[1].messages[-1].content)
    second_repair = json.loads(provider.calls[2].messages[-1].content)
    assert "JSON" in first_repair["validation_error"]
    assert "bool" in second_repair["validation_error"]
    assert invalid in [
        m.content for m in provider.calls[2].messages if m.role == "assistant"
    ]


@pytest.mark.parametrize("boss", [False, True])
def test_third_invalid_output_raises_after_exactly_two_repairs(
    tmp_path: Path, boss: bool
) -> None:
    reviewer, provider, events, _ = setup(tmp_path, ["bad JSON"] * 3 + [answer()])
    with pytest.raises(ReviewError, match="2 repairs"):
        if boss:
            area = Area(
                path=Path("tasks.md"), line=1, id="A1", title="Dungeon", boss=("S1",)
            )
            scenario = Scenario(
                path=Path("spec.md"),
                line=1,
                id="S1",
                title="Check",
                gherkin="Then works",
            )
            reviewer.evaluate_boss(area, scenarios=(scenario,), area_diff="", gates=())
        else:
            reviewer.review_room(room(), attempt_diff="", gates=())
    assert len(provider.calls) == 3
    assert not any(isinstance(e, Review | BossScenario | BossResult) for e in events)


@pytest.mark.parametrize(
    "bad",
    [
        answer() + "\n" + answer(),
        "[" + answer() + "]",
        '{"broken":' + answer(),
        '```json\n{"bad":}\n```',
    ],
)
def test_ambiguous_wrong_shape_or_broken_json_requires_repair(
    tmp_path: Path, bad: str
) -> None:
    reviewer, provider, _, _ = setup(tmp_path, [bad, answer()])
    assert reviewer.review_room(room(), attempt_diff="", gates=()).verdict == "pass"
    assert len(provider.calls) == 2


def room_with_items(*texts: str) -> Room:
    return room().model_copy(
        update={
            "checklist": tuple(
                ChecklistItem(
                    path=Path("tasks.md"), line=index + 10, text=text, checked=False
                )
                for index, text in enumerate(texts)
            )
        }
    )


@pytest.mark.parametrize(
    "returned",
    [
        "behavior works",
        "  Behavior \t works. ",
        "- [ ] BEHAVIOR WORKS!!!",
        "[x] Behavior works",
        "* [ ] behavior works…",
    ],
)
def test_checklist_normalization_preserves_original_text(
    tmp_path: Path, returned: str
) -> None:
    output = json.loads(answer())
    output["checklist"][0]["item"] = returned
    reviewer, _, events, _ = setup(tmp_path, json.dumps(output))
    result = reviewer.review_room(
        room_with_items("- [ ] Behavior works."), attempt_diff="", gates=()
    )
    assert result.verdict == "pass"
    assert [(i.item, i.ok) for i in result.checklist] == [
        ("- [ ] Behavior works.", True)
    ]
    event = next(e for e in events if isinstance(e, Review))
    assert [(i.item, i.ok) for i in event.checklist] == [
        ("- [ ] Behavior works.", True)
    ]


def test_normalized_matches_preserve_room_order_and_ignore_unknown_extras(
    tmp_path: Path,
) -> None:
    output = json.dumps(
        {
            "verdict": "pass",
            "checklist": [
                {"item": "SECOND WORKS!", "ok": False, "note": "second failed"},
                {"item": "unknown", "ok": True, "note": "ignore"},
                {"item": "first works", "ok": True, "note": "first verified"},
            ],
            "feedback": "",
        }
    )
    reviewer, _, events, _ = setup(tmp_path, output)
    result = reviewer.review_room(
        room_with_items("First works.", "Second works."), attempt_diff="", gates=()
    )
    assert result.verdict == "fail"
    event = next(e for e in events if isinstance(e, Review))
    assert [(i.item, i.ok, i.note) for i in event.checklist] == [
        ("First works.", True, "first verified"),
        ("Second works.", False, "second failed"),
    ]


def test_equal_counts_fall_back_to_position_for_rephrased_items(tmp_path: Path) -> None:
    output = json.dumps(
        {
            "verdict": "pass",
            "checklist": [
                {"item": "paraphrased first", "ok": False, "note": "first failed"},
                {"item": "paraphrased second", "ok": True, "note": "second verified"},
            ],
            "feedback": "",
        }
    )
    reviewer, _, events, _ = setup(tmp_path, output)
    result = reviewer.review_room(
        room_with_items("First works", "Second works"), attempt_diff="", gates=()
    )
    assert result.verdict == "fail"
    event = next(e for e in events if isinstance(e, Review))
    assert [(i.item, i.ok, i.note) for i in event.checklist] == [
        ("First works", False, "first failed"),
        ("Second works", True, "second verified"),
    ]


def test_missing_checklist_items_fail_without_repair_and_unknown_items_are_ignored(
    tmp_path: Path,
) -> None:
    reviewer, provider, events, _ = setup(
        tmp_path, answer().replace("Behavior works", "Unknown")
    )
    result = reviewer.review_room(
        room_with_items("First works", "Second works"), attempt_diff="", gates=()
    )
    assert result.verdict == "fail" and len(provider.calls) == 1
    event = next(e for e in events if isinstance(e, Review))
    assert [(i.item, i.ok, i.note) for i in event.checklist] == [
        ("First works", False, "not reviewed by the Paladin"),
        ("Second works", False, "not reviewed by the Paladin"),
    ]


def test_missing_item_preserves_other_matched_results(tmp_path: Path) -> None:
    reviewer, _, events, _ = setup(
        tmp_path, answer().replace("Behavior works", "Second works")
    )
    reviewer.review_room(
        room_with_items("First works", "Second works"), attempt_diff="", gates=()
    )
    event = next(e for e in events if isinstance(e, Review))
    assert [(i.item, i.ok) for i in event.checklist] == [
        ("First works", False),
        ("Second works", True),
    ]


def test_gate_failure_tail_reaches_paladin_and_returned_respawn_feedback(
    tmp_path: Path,
) -> None:
    import shlex
    import sys

    from specvania.agent.gates import GateRunner

    reviewer, provider, events, clock = setup(tmp_path, answer())
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "print('HEADER_ONLY'+'x'*5120); print('FAILED test_x'); raise SystemExit(1)"
    )
    runner = GateRunner(
        config=Config(gates=[command], limits=LimitsConfig(observation_kb=1)),
        root=tmp_path,
        clock=clock,
        emit=events.append,
        run_id="run",
        timeout=10000,
    )
    results = runner.run("T10")
    review = reviewer.review_room(room(), attempt_diff="", gates=results)
    payload = json.loads(provider.calls[0].messages[1].content)
    assert "FAILED test_x" in payload["gate_results"][0]["output_tail"]
    assert "HEADER_ONLY" not in payload["gate_results"][0]["output_tail"]
    assert len(payload["gate_results"][0]["output_tail"].encode()) <= 1024
    assert review.verdict == "fail" and "FAILED test_x" in review.feedback
    gate_event = next(e for e in events if isinstance(e, GateResult))
    assert (
        "FAILED test_x" in gate_event.output_summary
        and len(gate_event.output_summary) <= 200
    )
    assert "output_tail" not in gate_event.model_dump()
    assert len(next(e for e in events if isinstance(e, Review)).feedback) <= 200
