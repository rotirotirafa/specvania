"""T27 stamina: real engine/ledger, scripted models, portable event streams."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from test_engine import (
    Harness,
    action,
    boss,
    config,
    patch_cli,
    review,
    write_world,
)
from test_engine import harness as harness
from test_engine import ledger_url as ledger_url
from test_studio_flow import project as project
from test_studio_flow import studio_ledger_url as studio_ledger_url
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.config import Config, ConfigError, load_config
from specvania.provider.fake import ScriptedProvider
from specvania.run.engine import PauseDecision
from specvania.run.events import Cooldown, ModelCall, RedactionGuard, Role, RunPaused
from specvania.run.jsonl import decode_events, encode_events
from specvania.run.state import fold_events


def budget_config(budget: int = 200000) -> Config:
    settings = config()
    return settings.model_copy(
        update={"limits": settings.limits.model_copy(update={"token_budget": budget})}
    )


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "true", '"SECRET_INPUT"'])
def test_budget_config_rejects_invalid_values_without_echo(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"[limits]\ntoken_budget = {value}\n")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "limits.token_budget" in str(caught.value)
    assert "SECRET_INPUT" not in str(caught.value)


def test_budget_default_and_positive_integer(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("")
    assert load_config(path).limits.token_budget == 1000000
    path.write_text("[limits]\ntoken_budget = 200000\n")
    assert load_config(path).limits.token_budget == 200000


@pytest.mark.parametrize(
    "boundary",
    ["warrior", "paladin", "boss", "repair", "paladin_repair", "boss_repair"],
)
def test_budget_pauses_before_each_kind_of_call_and_abort(
    harness: Harness, boundary: str
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    expensive = action("write_file", path="T01.txt", content="built\n").model_copy(
        update={"tokens_in": 100000, "tokens_out": 100000}
    )
    if boundary == "paladin":
        script = [action().model_copy(update={"tokens_in": 200000, "tokens_out": 0})]
    elif boundary == "boss":
        script = [
            action(),
            review().model_copy(update={"tokens_in": 199988, "tokens_out": 7}),
        ]
    elif boundary == "repair":
        script = [expensive.model_copy(update={"content": "invalid action"})]
    elif boundary == "paladin_repair":
        script = [
            action(),
            expensive.model_copy(
                update={"content": "invalid review", "tokens_in": 99995}
            ),
        ]
    elif boundary == "boss_repair":
        script = [
            action(),
            review(),
            expensive.model_copy(
                update={"content": "invalid boss", "tokens_in": 99983}
            ),
        ]
    else:
        script = [expensive]
    seen = []
    engine = harness.engine(script, settings=budget_config())

    def decide(pause: RunPaused) -> PauseDecision:
        provider = engine.provider
        assert isinstance(provider, ScriptedProvider)
        seen.append((pause.reason, len(provider.calls)))
        return "abort"

    engine.decide = decide
    state = engine.run(directory)
    assert state.status == "aborted"
    assert seen == [("stamina exhausted", len(script))]
    assert state.tokens_spent == 200000
    assert state.token_budget == 200000
    assert harness.ledger.list_events(engine.run_id)[-1].type == "run_finished"


def test_rest_uses_original_budget_twice_and_continues_same_call(
    harness: Harness,
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    script = [
        action("write_file", path="T01.txt", content="first\n").model_copy(
            update={"tokens_in": 100000, "tokens_out": 100000}
        ),
        action().model_copy(update={"tokens_in": 20000, "tokens_out": 30000}),
        review(),
        boss(),
    ]
    engine = harness.engine(script, settings=budget_config())
    call_counts = []

    def decide(pause: RunPaused) -> PauseDecision:
        assert pause.reason == "stamina exhausted"
        assert isinstance(engine.provider, ScriptedProvider)
        call_counts.append(len(engine.provider.calls))
        return "retry"

    engine.decide = decide
    state = engine.run(directory)
    events = harness.ledger.list_events(engine.run_id)
    assert state.status == "finished" and call_counts == [1, 2]
    assert [e.token_budget for e in events if e.type == "stamina_restored"] == [
        250000,
        300000,
    ]
    assert len([e for e in events if e.type == "attempt_started"]) == 1
    assert state.token_budget == 300000 and state.tokens_spent == 250012
    assert state.tokens_spent_by_role.mage == 0
    assert state.tokens_spent_by_role.warrior == 250000
    assert state.tokens_spent_by_role.paladin == 12
    assert decode_events(encode_events(events, harness.guard), harness.guard) == tuple(
        events
    )


def test_engine_reasks_skip_without_skipping_or_calling(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine(
        [
            action().model_copy(update={"tokens_in": 200000, "tokens_out": 0}),
            review(),
            boss(),
        ],
        settings=budget_config(),
    )
    choices: Iterator[PauseDecision] = iter(("skip", "retry"))
    seen = []

    def decide(pause: RunPaused) -> PauseDecision:
        assert isinstance(engine.provider, ScriptedProvider)
        seen.append(len(engine.provider.calls))
        return next(choices)

    engine.decide = decide
    assert engine.run(directory).status == "finished"
    assert seen == [1, 1]
    assert not any(
        e.type == "room_skipped" for e in harness.ledger.list_events(engine.run_id)
    )


def test_budget_pause_with_no_decision_stays_paused(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine([action()], settings=budget_config(5))
    state = engine.run(directory)
    assert state.status == "paused" and state.reason == "stamina exhausted"


def test_cli_stamina_prompt_offers_only_retry_abort(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    (harness.root / ".specvania/config.toml").write_text("[limits]\ntoken_budget = 5\n")
    provider = ScriptedProvider([action()])
    patch_cli(harness, monkeypatch, provider)
    result = CliRunner().invoke(
        cli.create_app(), ["run", str(directory), "--allow-dirty"], input="abort\n"
    )
    assert result.exit_code == 1 and "stamina exhausted" in result.output
    assert (
        "retry" in result.output
        and "abort" in result.output
        and "skip" not in result.output
    )
    assert len(provider.calls) == 1


def test_old_demo_and_plan_tokens_uncapped_cooldown_preserves_stamina() -> None:
    guard = RedactionGuard()
    events = decode_events(
        (
            Path(__file__).parents[1] / "src/specvania/examples/demo/events.jsonl"
        ).read_text(),
        guard,
    )
    state = fold_events(events)
    assert state.token_budget is None
    assert state.tokens_spent == sum(
        e.tokens_in + e.tokens_out for e in events if e.type == "model_call"
    )
    clock = FakeClock()
    call = ModelCall(
        ts=clock.now(),
        run_id="plan",
        seq=1,
        role=Role.MAGE,
        model="shared",
        tokens_in=10,
        tokens_out=20,
        latency=0,
        status=200,
    )
    cooldown = Cooldown(
        ts=clock.now(),
        run_id="plan",
        seq=2,
        role=Role.WARRIOR,
        model="shared",
        seconds=10,
        reason="backoff",
    )
    before, after = fold_events([call]), fold_events([call, cooldown])
    assert before.token_budget == after.token_budget is None
    assert before.tokens_spent == after.tokens_spent == 30
    assert before.tokens_spent_by_role == after.tokens_spent_by_role
    assert after.tokens_spent_by_role.mage == 30
    assert after.tokens_spent_by_role.warrior == 0


@pytest.mark.parametrize("choice", ["retry", "abort"])
def test_studio_rejects_stamina_skip_without_state_change_then_resolves(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    studio_ledger_url: str,
    choice: str,
) -> None:
    import json

    from test_studio_flow import (
        SPEC,
        TASKS,
        make_studio,
        prepare,
        ready,
        receive,
        send,
        socket,
    )

    from specvania.provider.client import Completion

    monkeypatch.setenv("SPECVANIA_DATABASE_URL", studio_ledger_url)
    prepare(project)
    path = project / ".specvania/config.toml"
    path.write_text(
        path.read_text().replace("token_budget = 1000000", "token_budget = 200000")
    )
    # A plan spends over the configured run budget and still finishes uncapped.
    draft = Completion(
        content=json.dumps({"spec_md": SPEC, "tasks_md": TASKS.split("### T02")[0]}),
        tokens_in=300000,
    )
    instance = make_studio(
        monkeypatch,
        [
            draft,
            action().model_copy(update={"tokens_in": 100000, "tokens_out": 100000}),
            review(),
            boss(),
        ],
    )
    with socket(instance) as ws:
        ready(ws, instance, project)
        assert instance.snapshot().state.token_budget is None
        assert instance.snapshot().state.tokens_spent == 300000
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        send(ws, instance, "start_run", slug="build-work", allow_dirty=True)
        receive(ws, "studio_state", phase="running", pause_request="stamina exhausted")
        before = instance.info(), instance.snapshot()
        send(ws, instance, "pause_decision", choice="skip")
        assert "retry or abort" in receive(ws, "approval_rejected")["reason"]
        assert (instance.info(), instance.snapshot()) == before
        send(ws, instance, "pause_decision", choice=choice)
        receive(ws, "studio_state", phase="finished")
        state = instance.snapshot().state
        assert state.status == ("finished" if choice == "retry" else "aborted")
        assert state.token_budget == (250000 if choice == "retry" else 200000)


def test_overspent_run_rests_until_budget_exceeds_spending(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine(
        [
            action().model_copy(update={"tokens_in": 200000, "tokens_out": 50000}),
            review(),
            boss(),
        ],
        settings=budget_config(),
    )
    call_counts: list[int] = []

    def decide(pause: RunPaused) -> PauseDecision:
        assert pause.reason == "stamina exhausted"
        assert isinstance(engine.provider, ScriptedProvider)
        call_counts.append(len(engine.provider.calls))
        return "retry"

    engine.decide = decide
    state = engine.run(directory)
    assert state.status == "finished" and call_counts == [1, 1]
    assert [
        event.token_budget
        for event in harness.ledger.list_events(engine.run_id)
        if event.type == "stamina_restored"
    ] == [250000, 300000]
    assert state.tokens_spent == 250012


def test_shared_model_role_tokens_and_cooldown_preserve_budget() -> None:
    from specvania.run.events import RunStarted

    clock = FakeClock()
    started = RunStarted(
        ts=clock.now(),
        run_id="shared",
        seq=1,
        feature="feature",
        branch="branch",
        token_budget=200000,
    )
    calls = [
        ModelCall(
            ts=clock.now(),
            run_id="shared",
            seq=index,
            role=role,
            model="shared",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency=0,
            status=200,
        )
        for index, role, tokens_in, tokens_out in [
            (2, Role.MAGE, 10, 20),
            (3, Role.WARRIOR, 40, 50),
            (4, Role.PALADIN, 60, 70),
        ]
    ]
    cooldown = Cooldown(
        ts=clock.now(),
        run_id="shared",
        seq=5,
        role=Role.WARRIOR,
        model="shared",
        seconds=10,
        reason="backoff",
    )
    before, after = (
        fold_events([started, *calls]),
        fold_events([started, *calls, cooldown]),
    )
    assert before.token_budget == after.token_budget == 200000
    assert before.tokens_spent == after.tokens_spent == 250
    assert before.tokens_spent_by_role == after.tokens_spent_by_role
    assert after.tokens_spent_by_role.model_dump() == {
        "mage": 30,
        "warrior": 90,
        "paladin": 130,
    }
    assert len(after.models) == 1
