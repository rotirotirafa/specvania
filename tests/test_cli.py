"""Acceptance tests for room T01's installed CLI and offline test harness."""

import importlib.metadata
import itertools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def invoke(*arguments: str, module: bool = False) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("specvania")
    assert executable is not None, "The specvania entry point must be installed"
    command = [sys.executable, "-m", "specvania"] if module else [executable]
    return subprocess.run(
        [*command, *arguments],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
    )


def test_help_lists_all_spec_commands() -> None:
    result = invoke("--help")
    assert result.returncode == 0, result.stderr
    for command in (
        "init",
        "doctor",
        "plan",
        "lint",
        "run",
        "replay",
        "status",
        "history",
        "bug",
        "export",
    ):
        assert command in result.stdout.split()


@pytest.mark.parametrize("module", [False, True])
def test_version_matches_package_metadata(module: bool) -> None:
    result = invoke("--version", module=module)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == importlib.metadata.version("specvania")
    assert os.environ["NVIDIA_API_KEY"] not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "arguments",
    [],
)
def test_commands_are_explicit_stubs(
    arguments: tuple[str, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = invoke(*arguments)
    assert result.returncode == 2, result.stderr
    assert result.stdout.strip() == "not implemented yet"
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []
    assert os.environ["NVIDIA_API_KEY"] not in result.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ("bug", "--help"),
        ("init", "--help"),
        ("doctor", "--help"),
        ("plan", "--help"),
        ("lint", "--help"),
        ("run", "--help"),
        ("replay", "--help"),
        ("status", "--help"),
        ("history", "--help"),
        ("export", "--help"),
        ("bug", "add", "--help"),
        ("bug", "list", "--help"),
        ("bug", "close", "--help"),
    ],
)
def test_command_help_exits_successfully(arguments: tuple[str, ...]) -> None:
    result = invoke(*arguments)
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
    assert "not implemented yet" not in result.stdout
    if arguments == ("bug", "--help"):
        assert {"add", "list", "close"} <= set(result.stdout.split())


def test_python_module_help() -> None:
    result = invoke("--help", module=True)
    assert result.returncode == 0, result.stderr
    assert "specvania" in result.stdout


def test_ci_matrix_runs_all_local_gates() -> None:
    # JSON is a YAML subset, so the workflow can be checked without a YAML dependency.
    workflow_path = Path(__file__).parents[1] / ".github/workflows/ci.yml"
    assert workflow_path.is_file(), "The CI workflow must exist"
    workflow = json.loads(workflow_path.read_text())
    assert {"push", "pull_request"} <= set(workflow["on"])
    job = workflow["jobs"]["checks"]
    matrix = job["strategy"]["matrix"]
    assert set(itertools.product(matrix["os"], matrix["python-version"])) == {
        ("ubuntu-latest", "3.12"),
        ("ubuntu-latest", "3.13"),
        ("macos-latest", "3.12"),
        ("macos-latest", "3.13"),
    }
    assert job["runs-on"] == "${{ matrix.os }}"
    steps = job["steps"]
    assert any(
        step.get("with", {}).get("python-version") == "${{ matrix.python-version }}"
        for step in steps
    )
    commands = [step["run"] for step in steps if "run" in step]
    assert "uv sync --locked" in commands
    assert {
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run pyright",
        "uv run pytest -q",
    } <= set(commands)


@pytest.mark.parametrize(
    "operation",
    [
        'socket.socket().connect(("example.com", 443))',
        'socket.socket().connect_ex(("203.0.113.1", 443))',
        'socket.create_connection(("example.com", 443))',
        'socket.getaddrinfo("example.com", 443)',
        'socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", '
        '("203.0.113.1", 53))',
    ],
)
def test_socket_guard_fails_a_network_test(operation: str, tmp_path: Path) -> None:
    conftest = Path(__file__).with_name("conftest.py").read_text()
    # If the guard regresses, this sentinel fails before reaching the real transport.
    # The child must fail with the guard's message, never with this safety net.
    safety_net = """
import socket

class NoTransportSocket(socket.socket):
    def connect(self, *args, **kwargs):
        raise AssertionError("unguarded transport reached")
    def connect_ex(self, *args, **kwargs):
        raise AssertionError("unguarded transport reached")
    def sendto(self, *args, **kwargs):
        raise AssertionError("unguarded transport reached")

def pytest_configure():
    socket.socket = NoTransportSocket
    def no_dns(*args, **kwargs):
        raise AssertionError("unguarded DNS reached")
    socket.getaddrinfo = no_dns
"""
    (tmp_path / "conftest.py").write_text(conftest + safety_net)
    (tmp_path / "test_network.py").write_text(
        "import socket\n\ndef test_external_connection():\n    " + operation + "\n"
    )
    environment = dict(os.environ)
    environment.pop("SPECVANIA_TEST_DATABASE_URL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(tmp_path),
            "-c",
            "/dev/null",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Network access blocked by socket guard" in result.stdout
    assert "AssertionError: unguarded" not in result.stdout


@pytest.mark.parametrize("ledger_host", ["ledger.example", "127.0.0.1", "[::1]"])
def test_socket_guard_allows_only_configured_ledger_host(
    ledger_host: str, tmp_path: Path
) -> None:
    conftest = Path(__file__).with_name("conftest.py").read_text()
    # Replace the transport before fixtures run; exercising the allowlist stays offline.
    transport = """
import socket

class OfflineSocket(socket.socket):
    def connect(self, address):
        return None
    def connect_ex(self, address):
        return 0
    def sendto(self, data, *args):
        return len(data)

def pytest_configure():
    socket.socket = OfflineSocket
    def resolve(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", port, 0, 0)),
        ]
    socket.getaddrinfo = resolve
"""
    probe = """
import os
import socket
from urllib.parse import urlsplit
import pytest

def test_ledger_host_and_resolved_ips_are_allowed():
    host = urlsplit(os.environ["SPECVANIA_TEST_DATABASE_URL"]).hostname
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as connection:
        connection.connect((host, 5432))
        assert connection.connect_ex((host, 5432)) == 0
    with socket.create_connection((host, 5432)):
        pass
    for family, _, _, _, address in socket.getaddrinfo(host, 5432):
        with socket.socket(family) as connection:
            connection.connect(address)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        assert connection.sendto(b"ok", (host, 5432)) == 2

def test_unrelated_host_is_still_refused():
    with socket.socket() as connection:
        with pytest.raises(pytest.fail.Exception, match="Network access blocked"):
            connection.connect(("example.com", 443))
    with pytest.raises(pytest.fail.Exception, match="Network access blocked"):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(pytest.fail.Exception, match="Network access blocked"):
        socket.create_connection(("203.0.113.1", 443))
"""
    (tmp_path / "conftest.py").write_text(conftest + transport)
    (tmp_path / "test_allowlist.py").write_text(probe)
    environment = {
        **os.environ,
        "SPECVANIA_TEST_DATABASE_URL": f"postgresql+psycopg://test@{ledger_host}:5432/test",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(tmp_path),
            "-c",
            "/dev/null",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
