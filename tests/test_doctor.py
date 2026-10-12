from pathlib import Path

import pytest
import tomli_w
from typer.testing import CliRunner

from specvania import cli
from specvania.config import Config, ProviderConfig, RolesConfig
from specvania.provider.client import ProviderError
from specvania.provider.fake import ScriptedProvider


def configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPECVANIA_DATABASE_URL", raising=False)
    monkeypatch.setenv("CUSTOM_API_KEY", "doctor-fake-secret")
    (tmp_path / ".specvania").mkdir()
    config = Config(
        provider=ProviderConfig(api_key_env="CUSTOM_API_KEY"),
        roles=RolesConfig(mage=["a", "b"], warrior=["b"], paladin=["c"]),
    )
    (tmp_path / ".specvania/config.toml").write_text(
        tomli_w.dumps(config.model_dump(exclude_none=True))
    )


def catalog(monkeypatch: pytest.MonkeyPatch, models: tuple[str, ...]) -> None:
    monkeypatch.setattr(
        cli,
        "OpenAIProvider",
        lambda *args, **kwargs: ScriptedProvider([], models=models),
    )


def test_doctor_loads_config_marks_every_model_and_reports_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path, monkeypatch)
    catalog(monkeypatch, ("a", "b", "c"))
    result = CliRunner().invoke(cli.create_app(), ["doctor"])
    assert result.exit_code == 0, result.output
    for line in [
        "mage: a found",
        "mage: b found",
        "warrior: b found",
        "paladin: c found",
    ]:
        assert line in result.output
    assert "migration 0001" in result.output
    assert (tmp_path / ".specvania/ledger.db").is_file()
    assert "doctor-fake-secret" not in result.output


def test_doctor_missing_key_exits_nonzero_without_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path, monkeypatch)
    monkeypatch.delenv("CUSTOM_API_KEY")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Missing key must prevent provider construction")

    monkeypatch.setattr(cli, "OpenAIProvider", forbidden)
    result = CliRunner().invoke(cli.create_app(), ["doctor"])
    assert result.exit_code == 1
    assert "CUSTOM_API_KEY" in result.output and "missing" in result.output
    assert "Traceback" not in result.output


def test_doctor_missing_model_exits_nonzero_and_marks_all_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path, monkeypatch)
    catalog(monkeypatch, ("a", "c"))
    result = CliRunner().invoke(cli.create_app(), ["doctor"])
    assert result.exit_code == 1
    assert "mage: b missing" in result.output and "warrior: b missing" in result.output
    assert "paladin: c found" in result.output


def test_doctor_unreachable_ledger_exits_nonzero_without_url_or_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path, monkeypatch)
    catalog(monkeypatch, ("a", "b", "c"))
    url = "invalid://user:doctor-url-secret@invalid.invalid/ledger"
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", url)
    result = CliRunner().invoke(cli.create_app(), ["doctor"])
    assert result.exit_code == 1
    assert "ledger" in result.output.lower()
    assert url not in result.output and "doctor-url-secret" not in result.output
    assert (
        "doctor-fake-secret" not in result.output and "Traceback" not in result.output
    )


def test_doctor_catalog_failure_is_clear_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path, monkeypatch)

    class Unavailable(ScriptedProvider):
        def list_models(self) -> tuple[str, ...]:
            raise ProviderError(status=401)

    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: Unavailable([]))
    result = CliRunner().invoke(cli.create_app(), ["doctor"])
    assert result.exit_code == 1
    assert "401" in result.output
    assert (
        "doctor-fake-secret" not in result.output and "Traceback" not in result.output
    )


def test_doctor_missing_config_exits_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.create_app(), ["doctor"])
    assert result.exit_code == 1 and "config" in result.output.lower()
    assert "Traceback" not in result.output


def test_doctor_uses_configured_ledger_url_without_printing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path, monkeypatch)
    catalog(monkeypatch, ("a", "b", "c"))
    path = tmp_path / "custom-ledger.db"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", url)
    result = CliRunner().invoke(cli.create_app(), ["doctor"])
    assert result.exit_code == 0, result.output
    assert path.is_file()
    assert not (tmp_path / ".specvania/ledger.db").exists()
    assert "migration 0001" in result.output
    assert url not in result.output and str(path) not in result.output
    assert "doctor-fake-secret" not in result.output
