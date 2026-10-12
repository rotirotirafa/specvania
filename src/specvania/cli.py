"""Thin CLI registration; commands remain stubs until their rooms are cleared."""

import os
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from specvania.clock import SystemClock
from specvania.config import Config, ConfigError, initialize_config, load_config
from specvania.ledger.repo import Ledger, LedgerError
from specvania.provider.client import OpenAIProvider, ProviderError
from specvania.run.events import RedactionGuard, Role
from specvania.sdd.lint import WorldLintError, validate_directory
from specvania.sdd.world import WorldParseError
from specvania.ui.launcher import (
    BrowserLaunchError,
    DesktopLaunchError,
    UIMode,
    open_ui,
)


def launch_ui(
    port: int,
    view_token: str,
    control_token: str,
    *,
    config: Config,
    mode: UIMode | None = None,
) -> str:
    """Shared CLI wiring for command rooms, once their UI server is listening."""
    try:
        result = open_ui(
            port,
            view_token,
            control_token,
            mode=mode if mode is not None else config.ui.mode,
            desktop_path=config.desktop.path,
        )
    except (BrowserLaunchError, DesktopLaunchError) as error:
        Console(stderr=True).print(str(error), markup=False)
        raise typer.Exit(code=1) from None
    if result.notice is not None:
        Console().print(result.notice, markup=False, highlight=False, soft_wrap=True)
    return result.mode


def not_implemented() -> NoReturn:
    """Make the scaffold's unavailable behavior explicit."""
    Console().print("not implemented yet")
    raise typer.Exit(code=2)


def create_app() -> typer.Typer:
    """Register the surface from spec §6 without implementing later rooms."""
    app = typer.Typer(
        help="Your spec becomes a metroidvania map.",
        no_args_is_help=True,
        invoke_without_command=True,
        add_completion=False,
    )

    @app.callback()
    def root(
        show_version: Annotated[
            bool,
            typer.Option("--version", is_eager=True, help="Show the package version."),
        ] = False,
    ) -> None:
        if show_version:
            Console().print(version("specvania"))
            raise typer.Exit()

    @app.command("init", help="Create config in the current git repository.")
    def init(
        force: Annotated[
            bool, typer.Option("--force", help="Overwrite an existing config.")
        ] = False,
    ) -> None:
        try:
            path = initialize_config(Path.cwd(), force=force)
        except ConfigError as error:
            Console(stderr=True).print(str(error), markup=False)
            raise typer.Exit(code=1) from None
        Console().print(f"Created {path}", markup=False)

    @app.command("doctor", help="Check the API key, model chains and ledger.")
    def doctor() -> None:
        console = Console()
        root = Path.cwd()
        try:
            config = load_config(root / ".specvania/config.toml")
        except ConfigError as error:
            Console(stderr=True).print(str(error), markup=False)
            raise typer.Exit(code=1) from None
        failed = False
        guard = RedactionGuard()
        key = os.environ.get(config.provider.api_key_env, "")
        ledger_url = os.environ.get(config.ledger.database_url_env, "")
        if ledger_url:
            guard.register(ledger_url)
        if not key.strip():
            console.print(
                f"API key: {config.provider.api_key_env} missing", markup=False
            )
            failed = True
        else:
            guard.register(key)
            console.print(f"API key: {config.provider.api_key_env} set", markup=False)
            try:
                with OpenAIProvider(config.provider, api_key=key) as provider:
                    models = set(provider.list_models())
                for role in Role:
                    for model in getattr(config.roles, role.value):
                        found = model in models
                        console.print(
                            f"{role.value}: {model} {'found' if found else 'missing'}",
                            markup=False,
                        )
                        failed |= not found
            except ProviderError as error:
                console.print(str(error), markup=False)
                failed = True
        try:
            with Ledger.from_config(
                root, config.ledger, clock=SystemClock(), guard=guard
            ) as ledger:
                console.print(
                    f"Ledger: connected, migration {ledger.migration_version}"
                )
        except LedgerError as error:
            console.print(str(error), markup=False)
            failed = True
        if failed:
            raise typer.Exit(code=1)

    for name in (
        "plan",
        "run",
        "replay",
        "status",
        "history",
        "export",
    ):
        app.command(
            name,
            help="Not implemented yet.",
            context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
        )(not_implemented)

    @app.command("lint", help="Validate a world's format, doors and coverage.")
    def lint(
        directory: Annotated[Path, typer.Argument(help="World directory.")],
    ) -> None:
        try:
            validate_directory(directory)
            errors = ()
        except WorldParseError as error:
            errors = (error.diagnostic,)
        except WorldLintError as error:
            errors = error.diagnostics
        console = Console()
        if errors:
            for diagnostic in errors:
                console.print(
                    f"{diagnostic.path}:{diagnostic.line} "
                    f"{diagnostic.rule} {diagnostic.message}",
                    markup=False,
                    highlight=False,
                    soft_wrap=True,
                )
            raise typer.Exit(code=1)
        console.print("OK")

    bug = typer.Typer(help="Manage bugs in the Bestiary.", invoke_without_command=True)

    @bug.callback()
    def bug_root(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            not_implemented()

    for name in ("add", "list", "close"):
        bug.command(
            name,
            help="Not implemented yet.",
            context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
        )(not_implemented)
    app.add_typer(bug, name="bug")
    return app


def main() -> None:
    """Installed entry point and Python module entry point."""
    create_app()(prog_name="specvania")
