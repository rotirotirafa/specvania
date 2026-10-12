"""Thin CLI registration; commands remain stubs until their rooms are cleared."""

from importlib.metadata import version
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from specvania.config import ConfigError, initialize_config


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

    for name in (
        "doctor",
        "plan",
        "lint",
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
