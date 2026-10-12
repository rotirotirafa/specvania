import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from specvania.agent import gates as gates_module
from specvania.agent.gates import GateError, GateRunner
from specvania.clock import FakeClock
from specvania.config import Config, LimitsConfig
from specvania.run.events import Event, GateResult


def runner(
    root: Path, commands: list[str], *, timeout: float = 10, kb: int = 8
) -> tuple[GateRunner, list[Event], FakeClock]:
    events: list[Event] = []
    clock = FakeClock()
    return (
        GateRunner(
            config=Config(gates=commands, limits=LimitsConfig(observation_kb=kb)),
            root=root,
            clock=clock,
            emit=events.append,
            run_id="run",
            timeout=timeout,
        ),
        events,
        clock,
    )


def test_configured_gates_run_without_approval_and_emit_short_results(
    tmp_path: Path,
) -> None:
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "from pathlib import Path; Path('gate-ran').write_text('done'); "
        "print('ok'); raise SystemExit(7)"
    )
    gates, events, _ = runner(tmp_path, [command], timeout=10000)
    results = gates.run("T10")
    assert (tmp_path / "gate-ran").read_text() == "done"
    assert len(results) == len(events) == 1
    event = results[0].result
    assert isinstance(event, GateResult)
    assert events[0] == event
    assert event.room_id == "T10" and event.run_id == "run" and event.seq == 0
    assert event.exit_code == 7 and event.output_summary.strip() == "ok"
    assert len(event.command) <= 200


def test_gates_continue_after_failure_and_preserve_config_order(tmp_path: Path) -> None:
    gates, events, _ = runner(tmp_path, ["exit 3", "exit 0"], timeout=10000)
    results = gates.run("T10")
    assert [(r.result.command, r.result.exit_code) for r in results] == [
        ("exit 3", 3),
        ("exit 0", 0),
    ]
    assert events == [r.result for r in results]


def test_gate_output_is_bounded_summary_not_full_output(tmp_path: Path) -> None:
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote("print('é'*10000)")
    gates, _, _ = runner(tmp_path, [command], timeout=10000)
    result = gates.run("T10")[0].result
    assert result.exit_code == 0
    assert len(result.output_summary) <= 200
    assert result.output_summary.startswith("[truncated]")


def test_gate_timeout_uses_clock_kills_process_group_and_emits_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    killed: list[tuple[int, int]] = []
    waited: list[bool] = []

    class RunningProcess:
        pid = 987654
        returncode = None

        def poll(self) -> None:
            return None

        def wait(self) -> int:
            waited.append(True)
            return -9

    def launch(*args: Any, **kwargs: Any) -> RunningProcess:
        assert kwargs["cwd"] == tmp_path
        assert kwargs["start_new_session"] is True
        return RunningProcess()

    monkeypatch.setattr(gates_module.subprocess, "Popen", launch)
    monkeypatch.setattr(
        gates_module.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )
    gates, events, clock = runner(tmp_path, ["slow gate"], timeout=0.12)
    before = clock.now()
    result = gates.run("T10")[0].result
    assert (clock.now() - before).total_seconds() == pytest.approx(0.12)
    assert result.exit_code != 0 and "timed out" in result.output_summary
    assert killed and killed[0][0] == 987654 and waited == [True]
    assert events == [result]


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_is_typed_error(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(GateError):
        runner(tmp_path, [], timeout=timeout)


def test_no_configured_gates_produces_no_events(tmp_path: Path) -> None:
    gates, events, _ = runner(tmp_path, [])
    assert gates.run("T10") == () and events == []


def test_gate_launch_failure_emits_failure_and_runs_remaining_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch = gates_module.subprocess.Popen

    def failing(command: str, **kwargs: Any) -> Any:
        if command == "unstartable":
            raise OSError("PRIVATE_OS_ERROR")
        return launch(command, **kwargs)

    monkeypatch.setattr(gates_module.subprocess, "Popen", failing)
    gates, events, _ = runner(tmp_path, ["unstartable", "exit 0"], timeout=10000)
    results = gates.run("T10")
    assert results[0].result.exit_code != 0 and results[1].result.exit_code == 0
    assert results[0].result.output_summary.startswith("Cannot start gate")
    assert "PRIVATE_OS_ERROR" not in results[0].result.output_summary
    assert events == [r.result for r in results]


def test_large_whitespace_output_still_has_truncation_marker(tmp_path: Path) -> None:
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote("print(' '*10000)")
    gates, _, _ = runner(tmp_path, [command], timeout=10000)
    summary = gates.run("T10")[0].result.output_summary
    assert len(summary) <= 200 and summary.startswith("[truncated]")


def test_gate_returns_bounded_failure_tail_and_emits_only_short_summary(
    tmp_path: Path,
) -> None:
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "print('HEADER_ONLY' + 'x'*5120); print('FAILED test_x'); raise SystemExit(1)"
    )
    gates, events, _ = runner(tmp_path, [command], timeout=10000, kb=1)
    execution = gates.run("T10")[0]
    assert execution.result.exit_code == 1
    assert execution.output_tail.endswith("FAILED test_x\n")
    assert "FAILED test_x" in execution.result.output_summary
    assert execution.output_tail.startswith("[truncated]")
    assert len(execution.output_tail.encode()) <= 1024
    assert len(execution.result.output_summary) <= 200
    assert "HEADER_ONLY" not in execution.output_tail
    assert events == [execution.result]
    assert "output_tail" not in events[0].model_dump()


def test_gate_tail_starts_on_utf8_boundary_and_obeys_configured_limit(
    tmp_path: Path,
) -> None:
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "print('é漢🙂'*5000 + 'FINAL')"
    )
    gates, _, _ = runner(tmp_path, [command], timeout=10000, kb=2)
    execution = gates.run("T10")[0]
    assert len(execution.output_tail.encode()) <= 2048
    assert execution.output_tail.endswith("FINAL\n")
    assert "�" not in execution.output_tail
    assert "FINAL" in execution.result.output_summary
