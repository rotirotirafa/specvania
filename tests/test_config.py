"""Acceptance evidence for T02's config and init boundary."""

import os
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specvania.cli import create_app


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # A real, empty git repository without running any git-writing command.
    git_dir = tmp_path / ".git"
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_defaults_match_binding_decisions(repository: Path) -> None:
    from specvania.config import load_config

    result = CliRunner().invoke(create_app(), ["init"])
    assert result.exit_code == 0, result.output
    path = repository / ".specvania/config.toml"
    expected = {
        "provider": {
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
            "timeout_s": 300.0,
        },
        "roles": {
            "mage": [
                "moonshotai/kimi-k3",
                "z-ai/glm-5.3",
                "nvidia/nemotron-3-ultra-550b-a55b",
            ],
            "warrior": [
                "z-ai/glm-5.3",
                "moonshotai/kimi-k2.6",
                "deepseek-ai/deepseek-v4.1-flash",
            ],
            "paladin": [
                "nvidia/nemotron-3-ultra-550b-a55b",
                "deepseek-ai/deepseek-v4.1-flash",
                "z-ai/glm-5.3-flash",
            ],
        },
        "limits": {
            "rpm": 40,
            "lives": 3,
            "max_steps": 25,
            "observation_kb": 8,
            "failures_before_fallback": 3,
        },
        "gates": [],
        "ledger": {
            "sqlite_path": ".specvania/ledger.db",
            "database_url_env": "SPECVANIA_DATABASE_URL",
        },
        "ui": {"host": "127.0.0.1", "port": 0, "mode": "auto"},
        "desktop": {},
    }
    assert tomllib.loads(path.read_text()) == expected
    assert load_config(path).model_dump(exclude_none=True) == expected


def test_init_prints_long_path_on_one_line(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = repository / ("long-repository-path-" * 8)
    nested.mkdir()
    (repository / ".git").rename(nested / ".git")
    monkeypatch.chdir(nested)
    result = CliRunner().invoke(create_app(), ["init"], env={"COLUMNS": "40"})
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        f"Created {nested / '.specvania/config.toml'}"
    ]


def test_cli_config_error_prints_long_path_on_one_line(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = repository / ("long-repository-path-" * 8)
    nested.mkdir()
    (repository / ".git").rename(nested / ".git")
    monkeypatch.chdir(nested)
    result = CliRunner().invoke(create_app(), ["doctor"], env={"COLUMNS": "40"})
    assert result.exit_code == 1
    assert result.output.splitlines() == [
        f"Cannot read config at {nested / '.specvania/config.toml'}."
    ]


@pytest.mark.parametrize(
    "section", [None, "provider", "roles", "limits", "ledger", "ui", "desktop"]
)
def test_unknown_keys_are_rejected_readably(
    section: str | None, tmp_path: Path
) -> None:
    from specvania.config import ConfigError, load_config

    path = tmp_path / "config.toml"
    prefix = f"[{section}]\n" if section else ""
    path.write_text(prefix + 'unexpected = "secret-value-must-not-be-echoed"\n')
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    message = str(caught.value)
    assert "unexpected" in message
    assert "Extra inputs are not permitted" in message
    assert "secret-value-must-not-be-echoed" not in message
    if section:
        assert section in message


@pytest.mark.parametrize("role", ["mage", "warrior", "paladin"])
def test_empty_role_chains_are_rejected_readably(role: str, tmp_path: Path) -> None:
    from specvania.config import ConfigError, load_config

    path = tmp_path / "config.toml"
    path.write_text(f"[roles]\n{role} = []\n")
    with pytest.raises(ConfigError, match=rf"roles\.{role}.*at least 1"):
        load_config(path)


def test_custom_config_round_trips_without_reordering(tmp_path: Path) -> None:
    from specvania.config import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        'gates = ["pytest -q", "ruff check ."]\n'
        '[provider]\nbase_url = "http://localhost:11434/v1"\n'
        'api_key_env = "LOCAL_KEY"\n'
        '[roles]\nmage = ["second", "first"]\n'
        'warrior = ["code"]\npaladin = ["review"]\n'
        "[limits]\nrpm = 10\nlives = 2\nmax_steps = 12\n"
        "observation_kb = 4\nfailures_before_fallback = 2\n"
        '[ledger]\nsqlite_path = "custom/ledger.db"\n'
        'database_url_env = "TEAM_LEDGER"\n'
        '[ui]\nhost = "127.0.0.1"\nport = 8765\nmode = "desktop"\n'
        '[desktop]\npath = "/Applications/specvania.app"\n'
    )
    config = load_config(path)
    assert config.provider.base_url == "http://localhost:11434/v1"
    assert config.provider.api_key_env == "LOCAL_KEY"
    assert config.roles.mage == ["second", "first"]
    assert config.roles.warrior == ["code"]
    assert config.roles.paladin == ["review"]
    assert config.gates == ["pytest -q", "ruff check ."]
    assert config.limits.model_dump() == {
        "rpm": 10,
        "lives": 2,
        "max_steps": 12,
        "observation_kb": 4,
        "failures_before_fallback": 2,
    }
    assert config.ledger.sqlite_path == "custom/ledger.db"
    assert config.ledger.database_url_env == "TEAM_LEDGER"
    assert config.ui.model_dump() == {
        "host": "127.0.0.1",
        "port": 8765,
        "mode": "desktop",
    }
    assert config.desktop.path == "/Applications/specvania.app"


def test_init_outside_repository_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(create_app(), ["init"])
    assert result.exit_code != 0
    assert "git repository" in result.output
    assert "Traceback" not in result.output
    assert list(tmp_path.iterdir()) == []


def test_init_preserves_existing_config_until_forced(repository: Path) -> None:
    from specvania.config import load_config

    runner = CliRunner()
    assert runner.invoke(create_app(), ["init"]).exit_code == 0
    path = repository / ".specvania/config.toml"
    path.write_text("[limits]\nlives = 7\n")
    ignore = repository / ".gitignore"
    ignore_before = ignore.read_bytes()
    result = runner.invoke(create_app(), ["init"])
    assert result.exit_code != 0
    assert "--force" in result.output
    assert "Traceback" not in result.output
    assert path.read_text() == "[limits]\nlives = 7\n"
    assert ignore.read_bytes() == ignore_before
    result = runner.invoke(create_app(), ["init", "--force"])
    assert result.exit_code == 0, result.output
    assert load_config(path).limits.lives == 3


@pytest.mark.parametrize(
    "existing",
    ["", "# owner rules\n*.pyc", ".specvania/ledger.db*\n", ".specvania/runs/\n"],
)
def test_init_ignore_lines_are_idempotent(repository: Path, existing: str) -> None:
    ignore = repository / ".gitignore"
    ignore.write_text(existing)
    runner = CliRunner()
    assert runner.invoke(create_app(), ["init"]).exit_code == 0
    first = ignore.read_text()
    for _ in range(2):
        assert runner.invoke(create_app(), ["init", "--force"]).exit_code == 0
        assert ignore.read_text() == first
    assert first.startswith(existing)
    lines = first.splitlines()
    assert lines.count(".specvania/ledger.db*") == 1
    assert lines.count(".specvania/runs/") == 1


def test_init_from_subdirectory_writes_at_repository_root(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = repository / "src/deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    result = CliRunner().invoke(create_app(), ["init"])
    assert result.exit_code == 0, result.output
    assert (repository / ".specvania/config.toml").is_file()
    assert not (nested / ".specvania").exists()
    assert not (nested / ".gitignore").exists()


def test_init_stores_only_env_names_and_does_not_leak_values(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from specvania.config import load_config

    key = os.environ["NVIDIA_API_KEY"]
    ledger_url = "postgresql+psycopg://user:fake-password@localhost/team"
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", ledger_url)
    for arguments in (["init"], ["init"], ["init", "--force"]):
        result = CliRunner().invoke(create_app(), arguments)
        path = repository / ".specvania/config.toml"
        config = load_config(path)
        for text in (path.read_text(), config.model_dump_json(), result.output):
            assert key not in text
            assert ledger_url not in text
            assert "fake-password" not in text
        assert config.provider.api_key_env == "NVIDIA_API_KEY"
        assert config.ledger.database_url_env == "SPECVANIA_DATABASE_URL"


@pytest.mark.parametrize(
    "document, field",
    [
        ('[provider]\napi_key = "fake-secret"\n', "provider.api_key"),
        (
            '[ledger]\ndatabase_url = "postgresql://fake-secret"\n',
            "ledger.database_url",
        ),
        ('[provider]\napi_key_env = "fake-secret"\n', "provider.api_key_env"),
        (
            '[ledger]\ndatabase_url_env = "postgresql://fake-secret"\n',
            "ledger.database_url_env",
        ),
    ],
)
def test_secret_values_cannot_replace_env_names(
    tmp_path: Path, document: str, field: str
) -> None:
    from specvania.config import ConfigError, load_config

    path = tmp_path / "config.toml"
    path.write_text(document)
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert field in str(caught.value)
    assert "fake-secret" not in str(caught.value)


@pytest.mark.parametrize("document", [None, "[provider\n", "bad ="])
def test_missing_or_malformed_config_raises_typed_error(
    tmp_path: Path, document: str | None
) -> None:
    from specvania.config import ConfigError, load_config

    path = tmp_path / "config.toml"
    if document is not None:
        path.write_text(document)
    with pytest.raises(ConfigError, match="config"):
        load_config(path)


def test_config_instances_do_not_share_mutable_defaults() -> None:
    from specvania.config import Config

    first = Config()
    second = Config()
    first.roles.mage.clear()
    first.gates.append("pytest")
    assert second.roles.mage[0] == "moonshotai/kimi-k3"
    assert second.gates == []


@pytest.mark.parametrize(
    "field", ["rpm", "lives", "max_steps", "observation_kb", "failures_before_fallback"]
)
@pytest.mark.parametrize("value", ["0", "-1", "true", '"3"'])
def test_invalid_limits_are_rejected(tmp_path: Path, field: str, value: str) -> None:
    from specvania.config import ConfigError, load_config

    path = tmp_path / "config.toml"
    path.write_text(f"[limits]\n{field} = {value}\n")
    with pytest.raises(ConfigError, match=rf"limits\.{field}"):
        load_config(path)


@pytest.mark.parametrize(
    "document, field",
    [
        ('[roles]\nmage = [" "]\n', "roles.mage"),
        ('[ui]\nmode = "unknown"\n', "ui.mode"),
        ('[ui]\nhost = "0.0.0.0"\n', "ui.host"),
        ("[ui]\nport = -1\n", "ui.port"),
        ("[ui]\nport = 65536\n", "ui.port"),
        ('gates = [""]\n', "gates"),
    ],
)
def test_invalid_config_fields_are_rejected(
    tmp_path: Path, document: str, field: str
) -> None:
    from specvania.config import ConfigError, load_config

    path = tmp_path / "config.toml"
    path.write_text(document)
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert field in str(caught.value)


def test_partial_config_uses_defaults(tmp_path: Path) -> None:
    from specvania.config import load_config

    path = tmp_path / "config.toml"
    path.write_text('[ui]\nmode = "browser"\n')
    config = load_config(path)
    assert config.ui.mode == "browser"
    assert config.ui.port == 0
    assert config.limits.rpm == 40
    assert config.desktop.path is None


@pytest.mark.parametrize(
    "document, expected", [("", 300.0), ("[provider]\ntimeout_s = 427.5\n", 427.5)]
)
def test_provider_timeout_default_and_configured_value(
    tmp_path: Path, document: str, expected: float
) -> None:
    from specvania.config import load_config

    path = tmp_path / "config.toml"
    path.write_text(document)
    assert load_config(path).provider.timeout_s == expected


@pytest.mark.parametrize(
    "value, reason",
    [
        ("0.0", "greater than 0"),
        ("-17.125", "greater than 0"),
        ("nan", "finite number"),
        ("inf", "finite number"),
        ("-inf", "finite number"),
        ("true", "valid number"),
        ('"secret-timeout-value"', "valid number"),
    ],
)
def test_invalid_provider_timeout_is_rejected_without_echoing_input(
    tmp_path: Path, value: str, reason: str
) -> None:
    from specvania.config import ConfigError, load_config

    path = tmp_path / "config.toml"
    path.write_text(f"[provider]\ntimeout_s = {value}\n")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    message = str(caught.value)
    assert "provider.timeout_s" in message
    assert reason in message
    assert "input_value" not in message
    assert "secret-timeout-value" not in message
    assert "-17.125" not in message


def test_non_utf8_config_raises_typed_error(tmp_path: Path) -> None:
    from specvania.config import ConfigError, load_config

    path = tmp_path / "config.toml"
    path.write_bytes(b"\xff")
    with pytest.raises(ConfigError, match="config"):
        load_config(path)


def test_init_unwritable_target_reports_error_without_traceback(
    repository: Path,
) -> None:
    (repository / ".specvania").write_text("a file occupies the config directory")
    result = CliRunner().invoke(create_app(), ["init"])
    assert result.exit_code != 0
    assert "Cannot write" in result.output
    assert "Traceback" not in result.output
    assert not (repository / ".gitignore").exists()
