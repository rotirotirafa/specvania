"""Validated config and repository initialization, with no terminal output."""

import subprocess
import tomllib
from pathlib import Path
from typing import Annotated, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

type NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]
type EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
type ModelChain = Annotated[list[NonEmptyText], Field(min_length=1)]
type PositiveInt = Annotated[int, Field(strict=True, gt=0)]


class ConfigError(Exception):
    """A config or initialization error the player can fix."""


class ConfigModel(BaseModel):
    """Reject unknown fields at every config boundary without echoing values."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ProviderConfig(ConfigModel):
    base_url: NonEmptyText = "https://integrate.api.nvidia.com/v1"
    api_key_env: EnvName = "NVIDIA_API_KEY"


class RolesConfig(ConfigModel):
    mage: ModelChain = Field(
        default_factory=lambda: [
            "moonshotai/kimi-k3",
            "z-ai/glm-5.3",
            "nvidia/nemotron-3-ultra-550b-a55b",
        ]
    )
    warrior: ModelChain = Field(
        default_factory=lambda: [
            "z-ai/glm-5.3",
            "moonshotai/kimi-k2.6",
            "deepseek-ai/deepseek-v4.1-flash",
        ]
    )
    paladin: ModelChain = Field(
        default_factory=lambda: [
            "nvidia/nemotron-3-ultra-550b-a55b",
            "deepseek-ai/deepseek-v4.1-flash",
            "z-ai/glm-5.3-flash",
        ]
    )


class LimitsConfig(ConfigModel):
    rpm: PositiveInt = 40
    lives: PositiveInt = 3
    max_steps: PositiveInt = 25
    observation_kb: PositiveInt = 8
    failures_before_fallback: PositiveInt = 3


class LedgerConfig(ConfigModel):
    sqlite_path: NonEmptyText = ".specvania/ledger.db"
    database_url_env: EnvName = "SPECVANIA_DATABASE_URL"


class UIConfig(ConfigModel):
    host: Literal["127.0.0.1"] = "127.0.0.1"
    # Zero asks the operating system to allocate an available local port.
    port: Annotated[int, Field(strict=True, ge=0, le=65535)] = 0
    mode: Literal["auto", "browser", "desktop"] = "auto"


class DesktopConfig(ConfigModel):
    path: NonEmptyText | None = None


class Config(ConfigModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    roles: RolesConfig = Field(default_factory=RolesConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    gates: list[NonEmptyText] = Field(default_factory=list)
    ledger: LedgerConfig = Field(default_factory=LedgerConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    desktop: DesktopConfig = Field(default_factory=DesktopConfig)


def load_config(path: Path) -> Config:
    """Read TOML with stdlib tomllib and report field errors without input values."""
    try:
        with path.open("rb") as source:
            document = tomllib.load(source)
    except OSError as error:
        raise ConfigError(f"Cannot read config at {path}.") from error
    except (tomllib.TOMLDecodeError, UnicodeError) as error:
        raise ConfigError(f"Invalid TOML in config at {path}.") from error
    try:
        return Config.model_validate(document)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)
        )
        raise ConfigError(f"Invalid config at {path}: {details}") from error


def initialize_config(start: Path, *, force: bool = False) -> Path:
    """Write defaults and ignore entries at the current git repository's root."""
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ConfigError(
            "Cannot find the git repository; install git first."
        ) from error
    if result.returncode != 0:
        raise ConfigError("init must run inside a git repository.")
    root = Path(result.stdout.strip())
    path = root / ".specvania/config.toml"
    if path.exists() and not force:
        raise ConfigError("Config already exists; use init --force to overwrite it.")

    ignore_path = root / ".gitignore"
    try:
        existing = ignore_path.read_text() if ignore_path.exists() else ""
        lines = existing.splitlines()
        missing = [
            pattern
            for pattern in (".specvania/ledger.db*", ".specvania/runs/")
            if pattern not in lines
        ]
        updated = existing
        if missing:
            if updated and not updated.endswith("\n"):
                updated += "\n"
            updated += "\n".join(missing) + "\n"

        content = tomli_w.dumps(Config().model_dump(exclude_none=True))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("w" if force else "x") as destination:
                destination.write(content)
        except FileExistsError as error:
            raise ConfigError(
                "Config already exists; use init --force to overwrite it."
            ) from error
        if missing:
            ignore_path.write_text(updated)
    except (OSError, UnicodeError) as error:
        raise ConfigError(
            "Cannot write config or .gitignore; check file permissions and encoding."
        ) from error
    return path
