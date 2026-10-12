import json

import pytest
from pydantic import BaseModel

from specvania.agent.actions import ActionError, parse_action, summarize_args


@pytest.mark.parametrize(
    "name,args",
    [
        ("read_file", {"path": "note.txt", "start": 1, "end": 2}),
        ("list_dir", {"path": "."}),
        ("search", {"pattern": "needle", "glob": "*.py"}),
        ("write_file", {"path": "note.txt", "content": ""}),
        ("edit_file", {"path": "note.txt", "old": "old", "new": ""}),
        ("run_shell", {"command": "pytest -q"}),
        ("finish", {}),
    ],
)
def test_every_action_shape_validates_with_pydantic(name: str, args: dict) -> None:
    action = parse_action(json.dumps({"thought": "next", "action": name, "args": args}))
    assert isinstance(action, BaseModel) and isinstance(action.args, BaseModel)
    assert action.action == name
    assert action.args.model_dump(exclude_none=True) == args


@pytest.mark.parametrize(
    "name,args",
    [
        ("read_file", {}),
        ("read_file", {"path": "x", "start": 0}),
        ("read_file", {"path": "x", "start": True}),
        ("read_file", {"path": "x", "start": "2"}),
        ("read_file", {"path": "x", "start": 3, "end": 2}),
        ("read_file", {"path": "x", "end": 0}),
        ("list_dir", {"path": ""}),
        ("search", {"pattern": 12}),
        ("write_file", {"path": "x"}),
        ("edit_file", {"path": "x", "old": "x"}),
        ("run_shell", {"command": 2}),
        ("finish", {"unexpected": "private"}),
    ],
)
def test_invalid_action_arguments_are_rejected(name: str, args: dict) -> None:
    with pytest.raises(ActionError):
        parse_action(json.dumps({"thought": "next", "action": name, "args": args}))


def test_unknown_action_lists_all_valid_actions_without_echoing_input() -> None:
    with pytest.raises(ActionError) as error:
        parse_action('{"thought":"next","action":"private-unknown","args":{}}')
    assert "private-unknown" not in str(error.value)
    for name in (
        "read_file",
        "list_dir",
        "search",
        "write_file",
        "edit_file",
        "run_shell",
        "finish",
    ):
        assert name in str(error.value)


@pytest.mark.parametrize(
    "wrapper",
    [
        "{}",
        "```json\n{}\n```",
        "Here is my action:\n{}\nDone.",
        "Here is [the next action]:\n{}\nDone.",
    ],
)
def test_extracts_one_json_object_from_fences_or_prose(wrapper: str) -> None:
    payload = json.dumps(
        {"thought": 'escaped " and braces { }', "action": "finish", "args": {}}
    )
    assert parse_action(wrapper.format(payload)).action == "finish"


@pytest.mark.parametrize(
    "output",
    [
        "not JSON",
        '{"thought":"x","action":"finish","args":{}} {}',
        '[{"thought":"x","action":"finish","args":{}}]',
        '{"outer":{"thought":"x","action":"finish","args":{}}}',
        '{"thought":"x","action":"finish","args":{},"unfinished":',
        '{"thought":"x","action":"finish","action":"read_file","args":{}}',
        '{"thought":"x","action":"finish","args":{},"extra":true}',
        '{"action":"finish","args":{}}',
        '{"thought":null,"action":"finish","args":{}}',
    ],
)
def test_rejects_missing_multiple_nested_or_invalid_objects(output: str) -> None:
    with pytest.raises(ActionError):
        parse_action(output)


def test_thought_is_capped_at_500_characters() -> None:
    action = parse_action(
        json.dumps({"thought": "x" * 600, "action": "finish", "args": {}})
    )
    assert action.thought == "x" * 500


@pytest.mark.parametrize(
    "name,args",
    [
        ("read_file", {"path": "x" * 250, "start": 1}),
        ("list_dir", {"path": "notes"}),
        ("search", {"pattern": "PRIVATE-CONTENT", "glob": "*.txt"}),
        ("write_file", {"path": "note", "content": "PRIVATE-CONTENT"}),
        (
            "edit_file",
            {"path": "note", "old": "PRIVATE-CONTENT", "new": "OTHER-CONTENT"},
        ),
        ("run_shell", {"command": "printf PRIVATE-CONTENT > file"}),
        ("finish", {}),
    ],
)
def test_args_summaries_are_bounded_and_omit_contents(name: str, args: dict) -> None:
    action = parse_action(json.dumps({"thought": "next", "action": name, "args": args}))
    summary = summarize_args(action)
    assert len(summary) <= 200
    assert "PRIVATE-CONTENT" not in summary and "OTHER-CONTENT" not in summary
