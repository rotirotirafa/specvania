"""The shared output extractor preserves one value and rejects ambiguity."""

import json
from typing import Literal

import pytest

from specvania.provider.jsonout import ModelOutputError, extract_json_value


@pytest.mark.parametrize(
    ("output", "shape", "expected"),
    [
        (
            '```json\n{"nested": {"items": [1, 2]}}\n```',
            "{",
            {"nested": {"items": [1, 2]}},
        ),
        (
            'Ready: [{"question": "Brackets { inside } a string"}] Done.',
            "[",
            [{"question": "Brackets { inside } a string"}],
        ),
    ],
)
def test_extracts_single_top_level_value(
    output: str, shape: Literal["{", "["], expected: object
) -> None:
    assert json.loads(extract_json_value(output, shape)) == expected


@pytest.mark.parametrize(
    ("output", "shape"),
    [
        ("Nothing here", "{"),
        ('{} then {"other": true}', "{"),
        ('[{"nested": true}]', "{"),
        ('{"nested": []}', "["),
        ('{"broken": [}', "{"),
        ('{"unfinished": [', "{"),
    ],
)
def test_invalid_or_ambiguous_output_raises_typed_error(
    output: str, shape: Literal["{", "["]
) -> None:
    with pytest.raises(ModelOutputError):
        extract_json_value(output, shape)
