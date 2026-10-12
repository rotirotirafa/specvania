"""Extract a single JSON value from model output, including fences or prose."""

import json
from typing import Literal


class ModelOutputError(ValueError):
    """Model output has no unambiguous complete JSON value of the requested shape."""


def extract_json_value(output: str, shape: Literal["{", "["]) -> str:
    """Find one complete top-level value, never its nested objects or arrays."""
    candidates: list[str] = []
    stack: list[str] = []
    start = 0
    quoted = escaped = False
    for index, character in enumerate(output):
        if not stack:
            if character in "{[":
                start = index
                stack.append(character)
                quoted = escaped = False
            continue
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "{[":
            stack.append(character)
        elif character in "}]":
            if stack.pop() != ("{" if character == "}" else "["):
                raise ModelOutputError("Invalid JSON: mismatched brackets.")
            if not stack:
                candidate = output[start : index + 1]
                try:
                    json.loads(candidate)
                except ValueError:
                    continue
                candidates.append(candidate)
    if stack:
        raise ModelOutputError("Invalid JSON: incomplete top-level value.")
    if len(candidates) != 1 or not candidates[0].startswith(shape):
        kind = "object" if shape == "{" else "array"
        raise ModelOutputError(f"Expected exactly one top-level JSON {kind}.")
    return candidates[0]
