"""T32 authorized price provenance wiring stays compatible with old streams."""

import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from specvania.clock import FakeClock
from specvania.run.events import ModelPrice, RedactionGuard, RunStarted
from specvania.run.jsonl import decode_events, encode_events
from specvania.run.state import fold_events


@pytest.mark.parametrize("sourced", [False, True])
def test_price_provenance_round_trips_jsonl_and_RunState(sourced: bool) -> None:
    price = ModelPrice(
        model="test/model",
        input_per_mtok=Decimal("2.5"),
        output_per_mtok=Decimal("5"),
        currency="USD",
        source_url="https://example.invalid/pricing" if sourced else None,
        checked_on=date(2026, 10, 12) if sourced else None,
    )
    event = RunStarted(
        ts=FakeClock().now(),
        run_id="price-provenance",
        seq=1,
        feature="world",
        branch="local",
        prices=(price,),
    )
    guard = RedactionGuard()
    stream = encode_events([event], guard)
    payload = json.loads(stream)["prices"][0]
    if sourced:
        assert payload["source_url"] == "https://example.invalid/pricing"
        assert payload["checked_on"] == "2026-10-12"
    else:
        assert payload == {
            "model": "test/model",
            "input_per_mtok": "2.5",
            "output_per_mtok": "5",
            "currency": "USD",
        }
    events = decode_events(stream, guard)
    assert events == (event,)
    assert fold_events(events).prices == (price,)
    assert encode_events(events, guard) == stream


@pytest.mark.parametrize(
    "url",
    [
        "https://name:secret@example.invalid/pricing",
        "https://name@example.invalid",
        "file:///tmp/pricing",
        "invalid",
    ],
)
def test_price_source_rejects_credentials_and_non_http_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        ModelPrice(
            model="test/model",
            input_per_mtok=Decimal("2.5"),
            output_per_mtok=Decimal("5"),
            currency="USD",
            source_url=url,
        )
