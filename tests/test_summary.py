"""T12 summary, pricing and stop-hook evidence; all provider calls are scripted."""

import os
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_engine import Harness, action, boss, config, review, success, write_world
from test_engine import harness as harness
from test_engine import ledger_url as ledger_url

from specvania.clock import FakeClock
from specvania.pricing import PricingError, load_pricing
from specvania.run.engine import PauseDecision
from specvania.run.events import (
    AttemptStarted,
    Cooldown,
    Fallback,
    ModelCall,
    Role,
    RoomCleared,
    RoomEntered,
    RunPaused,
    SecretLeakError,
)
from specvania.run.summary import render_summary, summarize, write_summary


def pricing_file(root: Path) -> Path:
    path = root / ".specvania/pricing.toml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        '[[prices]]\nmodel = "priced"\ninput_per_mtok = 2.5\n'
        'output_per_mtok = 10\ncurrency = "USD"\n'
        'source_url = "https://prices.example/reference"\n'
        "checked_on = 2026-10-12\n"
    )
    return path


def test_summary_per_model_and_room_metrics() -> None:
    ts = FakeClock().now()
    events = [
        RoomEntered(ts=ts, run_id="run", seq=1, room_id="T01"),
        AttemptStarted(ts=ts, run_id="run", seq=2, room_id="T01", attempt=1),
        ModelCall(
            ts=ts,
            run_id="run",
            seq=3,
            role=Role.WARRIOR,
            model="m1",
            tokens_in=11,
            tokens_out=13,
            latency=1.25,
            status=429,
        ),
        Cooldown(
            ts=ts,
            run_id="run",
            seq=4,
            role=Role.WARRIOR,
            model="m1",
            seconds=5,
            reason="429",
        ),
        Fallback(
            ts=ts,
            run_id="run",
            seq=5,
            role=Role.WARRIOR,
            from_model="m1",
            to_model="m2",
            reason="exhausted",
        ),
        ModelCall(
            ts=ts,
            run_id="run",
            seq=6,
            role=Role.PALADIN,
            model="m1",
            tokens_in=17,
            tokens_out=19,
            latency=2.5,
            status=200,
        ),
        ModelCall(
            ts=ts,
            run_id="run",
            seq=7,
            role=Role.WARRIOR,
            model="m2",
            tokens_in=23,
            tokens_out=29,
            latency=0.5,
            status=200,
        ),
        # A player retry can reset its attempt number: count passes, not max(attempt).
        AttemptStarted(ts=ts, run_id="run", seq=8, room_id="T01", attempt=1),
        RoomCleared(ts=ts, run_id="run", seq=9, room_id="T01", commit="abc123"),
    ]
    result = summarize(events)
    assert [
        (
            m.model,
            m.calls,
            m.tokens_in,
            m.tokens_out,
            m.rate_limits,
            m.fallbacks,
            m.latency,
        )
        for m in result.models
    ] == [("m1", 2, 28, 32, 1, 1, 3.75), ("m2", 1, 23, 29, 0, 0, 0.5)]
    assert [(r.room_id, r.attempts, r.result, r.commit) for r in result.rooms] == [
        ("T01", 2, "cleared", "abc123")
    ]
    output = render_summary(result)
    assert "| m1 | 2 | 28 | 32 | 1 | 1 | 3.75 | not priced |" in output
    assert "| T01 | 2 | cleared | abc123 |" in output


@given(
    st.lists(
        st.tuples(
            st.sampled_from(["a", "b", "c"]),
            st.sampled_from(list(Role)),
            st.integers(min_value=0, max_value=10**7),
            st.integers(min_value=0, max_value=10**7),
            st.sampled_from([200, 429, 500]),
        ),
        max_size=80,
    )
)
def test_token_and_call_totals_equal_model_call_events(
    calls: list[tuple[str, Role, int, int, int]],
) -> None:
    ts = FakeClock().now()
    events = [
        Cooldown(
            ts=ts,
            run_id="run",
            seq=1,
            role=Role.WARRIOR,
            model="idle",
            seconds=5,
            reason="backoff",
        ),
        Fallback(
            ts=ts,
            run_id="run",
            seq=2,
            role=Role.WARRIOR,
            from_model="idle",
            to_model="a",
            reason="exhausted",
        ),
    ] + [
        ModelCall(
            ts=ts,
            run_id="run",
            seq=i,
            model=model,
            role=role,
            tokens_in=tin,
            tokens_out=tout,
            status=status,
            latency=0.5,
        )
        for i, (model, role, tin, tout, status) in enumerate(calls, 3)
    ]
    result = summarize(events)
    assert result.calls == len(calls)
    assert result.tokens_in == sum(c[2] for c in calls)
    assert result.tokens_out == sum(c[3] for c in calls)
    assert sum(m.calls for m in result.models) == len(calls)
    for model in {"a", "b", "c"}:
        metrics = next((m for m in result.models if m.model == model), None)
        selected = [c for c in calls if c[0] == model]
        if selected:
            assert metrics is not None
            assert metrics.calls == len(selected)
            assert metrics.tokens_in == sum(c[2] for c in selected)
            assert metrics.tokens_out == sum(c[3] for c in selected)


def test_without_pricing_no_currency_values(tmp_path: Path) -> None:
    ts = FakeClock().now()
    summary = summarize(
        [
            ModelCall(
                ts=ts,
                run_id="run",
                seq=1,
                role=Role.MAGE,
                model="m",
                tokens_in=100,
                tokens_out=200,
                latency=1,
                status=200,
            )
        ]
    )
    prices = load_pricing(tmp_path / "missing.toml")
    assert prices.prices == ()
    output = render_summary(summary, prices)
    assert "100" in output and "200" in output and "not priced" in output
    for value in ("USD", "EUR", "$", "€", "currency", "source_url", "checked_on"):
        assert value not in output


def test_priced_models_show_coins_source_date_and_unpriced_label(
    tmp_path: Path,
) -> None:
    prices = load_pricing(pricing_file(tmp_path))
    ts = FakeClock().now()
    events = [
        ModelCall(
            ts=ts,
            run_id="run",
            seq=i,
            role=Role.MAGE,
            model=model,
            tokens_in=1_000_000,
            tokens_out=500_000,
            latency=1,
            status=200,
        )
        for i, model in enumerate(["priced", "unpriced"], 1)
    ]
    assert prices.prices[0].coins(1_000_000, 500_000) == Decimal("7.5")
    output = render_summary(summarize(events), prices)
    assert "7.5 USD" in output
    assert "https://prices.example/reference" in output
    assert "2026-10-12" in output
    assert "| unpriced | 1 | 1000000 | 500000 | 0 | 0 | 1 | not priced |" in output


@pytest.mark.parametrize(
    "bad",
    [
        '[[prices]]\nmodel="m"\n',
        '[[prices]]\nmodel="m"\ninput_per_mtok=-1\noutput_per_mtok=0\n'
        'currency="USD"\nsource_url="https://prices.example"\nchecked_on=2026-10-12\n',
        '[[prices]]\nmodel="m"\ninput_per_mtok=nan\noutput_per_mtok=0\n'
        'currency="USD"\nsource_url="https://prices.example"\nchecked_on=2026-10-12\n',
        'unknown = "fake-secret"',
        "bad = [",
    ],
)
def test_invalid_pricing_is_typed_and_does_not_echo_input(
    tmp_path: Path, bad: str
) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text(bad)
    with pytest.raises(PricingError) as error:
        load_pricing(path)
    assert "fake-secret" not in str(error.value)


def test_pricing_rejects_duplicate_models(tmp_path: Path) -> None:
    path = pricing_file(tmp_path)
    path.write_text(path.read_text() * 2)
    with pytest.raises(PricingError):
        load_pricing(path)


@pytest.mark.parametrize("status", ["finished", "aborted", "paused"])
def test_engine_generates_summary_whenever_run_stops(
    harness: Harness, status: str
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    paused_snapshots: list[str] = []

    def decide(pause: RunPaused) -> PauseDecision | None:
        path = harness.root / f".specvania/runs/{pause.run_id}/summary.md"
        paused_snapshots.append(path.read_text())
        return "abort" if status == "aborted" else None

    engine = harness.engine(
        [*success("T01"), boss()]
        if status == "finished"
        else [action(), review(False)],
        settings=config(lives=1),
        decide=decide,
    )
    state = engine.run(directory)
    path = harness.root / f".specvania/runs/{engine.run_id}/summary.md"
    output = path.read_text()
    assert state.status == status
    assert f"Status: {status}" in output
    assert "Tokens in:" in output and "Tokens out:" in output
    assert "w1" in output and "p1" in output
    if status == "finished":
        commit = next(
            e.commit
            for e in harness.ledger.list_events(engine.run_id)
            if e.type == "room_cleared"
        )
        assert commit in output and "| T01 | 1 | cleared |" in output
    else:
        assert len(paused_snapshots) == 1 and "Status: paused" in paused_snapshots[0]
        assert "| T01 | 1 | failed |" in output
    assert os.environ["NVIDIA_API_KEY"] not in output


def test_summary_rejects_pricing_secret_before_writing(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine([*success("T01"), boss()])
    engine.run(directory)
    path = pricing_file(harness.root)
    secret = "fake-ui-control-token"
    path.write_text(path.read_text().replace("USD", secret).replace("priced", "w1"))
    destination = harness.root / f".specvania/runs/{engine.run_id}/summary.md"
    before = destination.read_text()
    with pytest.raises(SecretLeakError):
        write_summary(harness.root, harness.ledger, engine.run_id, secrets=(secret,))
    assert destination.read_text() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_url", "file:///private/prices"),
        ("source_url", "https://fake-secret@prices.example/reference"),
        ("checked_on", "not-a-date"),
        ("currency", ""),
        ("model", ""),
    ],
)
def test_pricing_requires_valid_metadata(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    path = pricing_file(tmp_path)
    lines = path.read_text().splitlines()
    path.write_text(
        "\n".join(
            f'{field} = "{value}"' if line.startswith(field + " =") else line
            for line in lines
        )
    )
    with pytest.raises(PricingError) as error:
        load_pricing(path)
    assert "fake-secret" not in str(error.value)


@pytest.mark.parametrize(
    "secret",
    [
        "fake-api-key",
        "fake-view-token",
        "fake-control-token",
        "postgresql+psycopg://fake:password@ledger.invalid/specvania",
    ],
)
def test_summary_rejects_all_secret_types(harness: Harness, secret: str) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine([*success("T01"), boss()])
    engine.run(directory)
    path = pricing_file(harness.root)
    path.write_text(path.read_text().replace("USD", secret).replace("priced", "w1"))
    destination = harness.root / f".specvania/runs/{engine.run_id}/summary.md"
    before = destination.read_text()
    with pytest.raises(SecretLeakError) as error:
        write_summary(harness.root, harness.ledger, engine.run_id, secrets=(secret,))
    assert secret not in str(error.value)
    assert destination.read_text() == before
