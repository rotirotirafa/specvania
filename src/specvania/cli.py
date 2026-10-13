"""Thin CLI registration; commands remain stubs until their rooms are cleared."""

import json
import logging
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from importlib.metadata import version
from importlib.resources import as_file, files
from pathlib import Path
from queue import Empty, Queue
from threading import Event as ThreadEvent
from threading import Thread
from typing import Annotated, Any, Literal, NoReturn, cast

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from typer.core import TyperCommand

from specvania.agent.approval import ApprovalError, ApprovalResponder, TerminalResponder
from specvania.agent.gates import GateError
from specvania.agent.sandbox import SandboxRefusal
from specvania.agent.tools import ToolError
from specvania.bugs import BugError, Bugs
from specvania.clock import SystemClock
from specvania.config import (
    Config,
    ConfigError,
    initialize_config,
    load_config,
    repository_root,
)
from specvania.ledger.repo import Ledger, LedgerError
from specvania.pricing import PricingError, load_pricing
from specvania.provider.client import OpenAIProvider, ProviderError
from specvania.run.bus import EventBus
from specvania.run.engine import Engine, EngineError, PauseDecision
from specvania.run.events import Event, RedactionGuard, Role, RunPaused, SecretLeakError
from specvania.run.gitops import GitError, GitOps
from specvania.run.summary import (
    SummaryError,
    export_run,
    history_report,
    latest_run,
    ledger_summary,
    render_summary,
    report_secrets,
    validate_report,
)
from specvania.sdd.lint import WorldLintError, validate_directory
from specvania.sdd.planner import PlanError, Question
from specvania.sdd.planner import plan as plan_world
from specvania.sdd.world import WorldParseError
from specvania.ui.launcher import (
    BrowserLaunchError,
    DesktopLaunchError,
    UIMode,
    open_ui,
)
from specvania.ui.server import TokenLogFormatter, UIServer, UIServerError, load_replay
from specvania.ui.studio import Studio, StudioError


class UICommand(TyperCommand):
    """Parse optional --ui values, remembering when config selects the mode."""

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        normalized = list(args)
        for index, value in enumerate(normalized):
            if value == "--":
                break
            if value.startswith("--ui="):
                ctx.meta["ui_use_config"] = False
            elif value == "--ui":
                bare = index + 1 == len(normalized) or normalized[index + 1] not in (
                    "auto",
                    "browser",
                    "desktop",
                )
                ctx.meta["ui_use_config"] = bare
                if bare:
                    normalized[index] = "--ui=auto"
        return super().parse_args(ctx, normalized)


def start_run_ui(server: UIServer, *, config: Config, mode: UIMode) -> Thread:
    """One launcher seam for live runs; serving stays on a daemon thread."""
    failures: Queue[Exception] = Queue()
    ready = ThreadEvent()

    def launch() -> None:
        launch_ui(
            server.port,
            server.view_token,
            server.control_token,
            config=config,
            mode=mode,
        )
        ready.set()

    def serve() -> None:
        try:
            server.serve(on_started=launch)
        except Exception as error:
            failures.put(error)

    worker = Thread(target=serve, name="specvania-ui", daemon=True)
    worker.start()
    deadline = server.clock.now().timestamp() + 10
    while not ready.is_set():
        try:
            raise failures.get_nowait()
        except Empty:
            pass
        if server.clock.now().timestamp() >= deadline:
            raise UIServerError("The live UI did not start; check the local port.")
        server.clock.sleep(0.01)
    try:
        raise failures.get_nowait()
    except Empty:
        pass
    return worker


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
        Console(stderr=True).print(
            str(error), markup=False, highlight=False, soft_wrap=True
        )
        raise typer.Exit(code=1) from None
    if result.notice is not None:
        Console().print(result.notice, markup=False, highlight=False, soft_wrap=True)
    return result.mode


def not_implemented() -> NoReturn:
    """Make the scaffold's unavailable behavior explicit."""
    Console().print("not implemented yet")
    raise typer.Exit(code=2)


@contextmanager
def report_ledger() -> Iterator[
    tuple[Path, Config, Ledger, RedactionGuard, tuple[str, ...]]
]:
    """Read-only commands need local config and a ledger, with no provider client."""
    registered = report_secrets(Config())
    try:
        root = GitOps(Path.cwd()).root
        config = load_config(root / ".specvania/config.toml")
        registered = report_secrets(config)
        guard = RedactionGuard()
        for secret in registered:
            guard.register(secret)
        with Ledger.from_config(
            root, config.ledger, clock=SystemClock(), guard=guard
        ) as ledger:
            yield root, config, ledger, guard, registered
    except (
        ConfigError,
        GitError,
        LedgerError,
        SummaryError,
        PricingError,
        SecretLeakError,
    ) as error:
        message = str(error)
        try:
            validate_report(message, registered)
        except SecretLeakError:
            message = "Report rejected: contains a registered secret"
        Console(stderr=True).print(
            message, markup=False, highlight=False, soft_wrap=True
        )
        raise typer.Exit(code=1) from None


def create_app() -> typer.Typer:
    """Register the surface from spec §6 without implementing later rooms."""
    app = typer.Typer(
        help="Your spec becomes a metroidvania map.",
        no_args_is_help=True,
        invoke_without_command=True,
        add_completion=False,
    )

    @app.command()
    def studio() -> None:
        """Serve the desktop game and print its private launch handshake."""
        instance = Studio(guard=RedactionGuard(), clock=SystemClock())
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            TokenLogFormatter(
                (
                    instance.server.view_token,
                    instance.server.control_token,
                )
            )
        )
        logging.basicConfig(handlers=[handler], level=logging.WARNING, force=True)

        def handshake() -> None:
            server = instance.server
            sys.stdout.write(
                json.dumps(
                    {
                        "port": server.port,
                        "view_token": server.view_token,
                        "control_token": server.control_token,
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            # All later library output belongs on stderr, including incidental
            # prints from future planner/engine integrations.
            sys.stdout = sys.stderr

        original_stdout = sys.stdout
        try:
            instance.serve(on_started=handshake)
        except (UIServerError, StudioError):
            Console(stderr=True).print(
                "Cannot start or stop the studio; check the local server."
            )
            raise typer.Exit(code=1) from None
        finally:
            sys.stdout = original_stdout

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
            Console(stderr=True).print(
                str(error), markup=False, highlight=False, soft_wrap=True
            )
            raise typer.Exit(code=1) from None
        Console().print(
            f"Created {path}", markup=False, highlight=False, soft_wrap=True
        )

    @app.command("doctor", help="Check the API key, model chains and ledger.")
    def doctor() -> None:
        console = Console()
        try:
            root = repository_root(Path.cwd())
            config = load_config(root / ".specvania/config.toml")
        except ConfigError as error:
            Console(stderr=True).print(
                str(error), markup=False, highlight=False, soft_wrap=True
            )
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
                console.print(str(error), markup=False, highlight=False, soft_wrap=True)
                failed = True
        try:
            with Ledger.from_config(
                root, config.ledger, clock=SystemClock(), guard=guard
            ) as ledger:
                console.print(
                    f"Ledger: connected, migration {ledger.migration_version}"
                )
        except LedgerError as error:
            console.print(str(error), markup=False, highlight=False, soft_wrap=True)
            failed = True
        if failed:
            raise typer.Exit(code=1)

    @app.command(
        "plan",
        cls=UICommand,
        help="Mage: Clarify an idea and write a verifiable world.",
    )
    def plan(
        ctx: typer.Context,
        idea: Annotated[str, typer.Argument(help="Feature idea for the Mage.")] = "",
        no_clarify: Annotated[
            bool, typer.Option("--no-clarify", help="Skip Clarify.")
        ] = False,
        from_bug: Annotated[
            str | None, typer.Option("--from-bug", help="Plan a fix for an open bug.")
        ] = None,
        ui: Annotated[
            Literal["auto", "browser", "desktop"] | None,
            typer.Option(
                "--ui", help="Watch planning; bare --ui uses configured ui.mode."
            ),
        ] = None,
    ) -> None:
        console = Console()
        root = Path.cwd()
        clock = SystemClock()
        guard = RedactionGuard()

        def answer(question: Question) -> str:
            console.print(f"Mage · {question.question}", markup=False)
            if question.options:
                console.print("Options: " + ", ".join(question.options), markup=False)
            return Prompt.ask("Answer", default=question.default, console=console)

        try:
            config = load_config(root / ".specvania/config.toml")
            key = os.environ.get(config.provider.api_key_env, "")
            if not key.strip():
                raise PlanError(f"API key: {config.provider.api_key_env} missing")
            guard.register(key)
            ledger_url = os.environ.get(config.ledger.database_url_env, "")
            if ledger_url:
                guard.register(ledger_url)
            bus = EventBus(guard)

            def started(run_id: str, snapshot: Callable[[], Sequence[Event]]) -> None:
                if ui is not None:
                    server = UIServer(
                        guard=guard,
                        clock=clock,
                        run_id=run_id,
                        bus=bus,
                        live_snapshot=snapshot,
                        port=config.ui.port,
                    )
                    start_run_ui(
                        server,
                        config=config,
                        mode=config.ui.mode if ctx.meta.get("ui_use_config") else ui,
                    )
                    console.print(
                        "Plan UI follows the bus only; the ledger is saved on success.",
                        markup=False,
                        highlight=False,
                        soft_wrap=True,
                    )

            with OpenAIProvider(config.provider, api_key=key) as provider:
                with Ledger.from_config(
                    root, config.ledger, clock=clock, guard=guard
                ) as ledger:
                    result = plan_world(
                        root=root,
                        idea=idea,
                        config=config,
                        provider=provider,
                        ledger=ledger,
                        clock=clock,
                        guard=guard,
                        answer=answer,
                        no_clarify=no_clarify,
                        from_bug_id=from_bug,
                        bus=bus if ui is not None else None,
                        on_started=started if ui is not None else None,
                    )
        except (
            ConfigError,
            LedgerError,
            ProviderError,
            PlanError,
            BugError,
            UIServerError,
        ) as error:
            Console(stderr=True).print(
                str(error), markup=False, highlight=False, soft_wrap=True
            )
            raise typer.Exit(code=1) from None
        console.print(
            f"Mage wrote {result.directory} (plan {result.run_id})",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

    @app.command(
        "run",
        cls=UICommand,
        help="Clear rooms in door order, with reviews and bosses.",
    )
    def run(
        ctx: typer.Context,
        directory: Annotated[Path, typer.Argument(help="World directory.")],
        allow_dirty: Annotated[
            bool, typer.Option("--allow-dirty", help="Allow an existing dirty tree.")
        ] = False,
        ui: Annotated[
            Literal["auto", "browser", "desktop"] | None,
            typer.Option(
                "--ui", help="Open the live map; bare --ui uses configured ui.mode."
            ),
        ] = None,
    ) -> None:
        console = Console()
        clock = SystemClock()
        guard = RedactionGuard()

        def decide(pause: RunPaused) -> PauseDecision:
            console.print(f"Run paused: {pause.reason}", markup=False)
            return cast(
                PauseDecision,
                Prompt.ask(
                    "Choose",
                    choices=["retry", "skip", "abort"],
                    default="abort",
                    console=console,
                ),
            )

        try:
            # Reject invalid worlds and dirty repositories before creating a client.
            validate_directory(directory)
            git = GitOps(Path.cwd())
            git.check(allow_dirty=allow_dirty)
            config = load_config(git.root / ".specvania/config.toml")
            key = os.environ.get(config.provider.api_key_env, "")
            if not key.strip():
                raise EngineError(f"API key: {config.provider.api_key_env} missing")
            guard.register(key)
            ledger_url = os.environ.get(config.ledger.database_url_env, "")
            if ledger_url:
                guard.register(ledger_url)
            bus = EventBus(guard)
            responder = TerminalResponder(
                0, lambda text: console.print(text, markup=False)
            )
            with OpenAIProvider(config.provider, api_key=key) as provider:
                with Ledger.from_config(
                    git.root, config.ledger, clock=clock, guard=guard
                ) as ledger:

                    def started(run_id: str) -> Sequence[ApprovalResponder] | None:
                        if ui is not None:
                            server = UIServer(
                                guard=guard,
                                clock=clock,
                                run_id=run_id,
                                ledger=ledger,
                                bus=bus,
                                port=config.ui.port,
                            )
                            start_run_ui(
                                server,
                                config=config,
                                mode=config.ui.mode
                                if ctx.meta.get("ui_use_config")
                                else ui,
                            )
                            return (server.desktop_responder,)
                        return None

                    state = Engine(
                        root=git.root,
                        config=config,
                        provider=provider,
                        ledger=ledger,
                        bus=bus,
                        clock=clock,
                        responders=[responder],
                        decide=decide,
                    ).run(directory, allow_dirty=allow_dirty, on_started=started)
        except WorldParseError as error:
            diagnostic = error.diagnostic
            Console(stderr=True).print(
                f"{diagnostic.path}:{diagnostic.line} {diagnostic.rule} "
                f"{diagnostic.message}",
                markup=False,
                highlight=False,
                soft_wrap=True,
            )
            raise typer.Exit(code=1) from None
        except WorldLintError as error:
            for diagnostic in error.diagnostics:
                Console(stderr=True).print(
                    f"{diagnostic.path}:{diagnostic.line} {diagnostic.rule} "
                    f"{diagnostic.message}",
                    markup=False,
                    highlight=False,
                    soft_wrap=True,
                )
            raise typer.Exit(code=1) from None
        except (
            ConfigError,
            LedgerError,
            ProviderError,
            EngineError,
            GitError,
            GateError,
            ToolError,
            ApprovalError,
            SandboxRefusal,
            UIServerError,
            SecretLeakError,
            PricingError,
            SummaryError,
        ) as error:
            Console(stderr=True).print(
                str(error), markup=False, highlight=False, soft_wrap=True
            )
            raise typer.Exit(code=1) from None
        console.print(f"Run {state.run_id}: {state.status}", markup=False)
        if state.status != "finished":
            raise typer.Exit(code=1)

    @app.command("status", help="Show rooms, attempts and per-model metrics of a run.")
    def status(
        run_id: Annotated[
            str | None, typer.Argument(help="Run id; latest when omitted.")
        ] = None,
    ) -> None:
        with report_ledger() as (root, _, ledger, _, secrets):
            if run_id is None:
                git = GitOps(root)
                project = ledger.add_project(root, git.origin_url())
                run_id = latest_run(ledger, project.id).id
            output = render_summary(
                ledger_summary(ledger, run_id),
                load_pricing(root / ".specvania/pricing.toml"),
                secrets=secrets,
            )
            Console().print(
                output, markup=False, highlight=False, soft_wrap=True, end=""
            )

    @app.command("history", help="Show features, spec versions, runs and open bugs.")
    def history(
        feature: Annotated[str | None, typer.Argument(help="Feature slug.")] = None,
    ) -> None:
        with report_ledger() as (root, _, ledger, _, secrets):
            project = ledger.add_project(root, GitOps(root).origin_url())
            output = history_report(ledger, project.id, feature)
            validate_report(output, secrets)
            Console().print(output, markup=False, highlight=False, soft_wrap=True)

    @app.command(
        "export", help="Write a run's events to .specvania/runs/<run-id>/events.jsonl."
    )
    def export(
        run_id: Annotated[str, typer.Argument(help="Run id to export.")],
    ) -> None:
        with report_ledger() as (root, _, ledger, guard, secrets):
            path = export_run(root, ledger, run_id, guard, secrets=secrets)
            Console().print(
                f"Exported {path}", markup=False, highlight=False, soft_wrap=True
            )

    @app.command("replay", help="Open a recorded run or the synthetic demo in the map.")
    def replay(
        source: Annotated[
            str, typer.Argument(help="Run id, path to events.jsonl, or demo.")
        ],
        speed: Annotated[
            Literal["1", "2", "4"], typer.Option("--speed", help="Playback speed.")
        ] = "1",
    ) -> None:
        guard = RedactionGuard()
        clock = SystemClock()
        config = Config()
        try:
            if source == "demo":
                resource = files("specvania").joinpath("examples/demo/events.jsonl")
                with as_file(resource) as path:
                    events = load_replay(path, guard=guard)
            elif source.endswith(".jsonl"):
                events = load_replay(Path(source), guard=guard)
            else:
                root = Path.cwd()
                config = load_config(root / ".specvania/config.toml")
                with Ledger.from_config(
                    root, config.ledger, clock=clock, guard=guard
                ) as ledger:
                    events = load_replay(source, guard=guard, ledger=ledger)
            if not events:
                raise UIServerError("Cannot replay an empty event stream.")
            server = UIServer(
                guard=guard,
                clock=clock,
                run_id=events[0].run_id,
                replay=events,
                speed=1 if speed == "1" else 2 if speed == "2" else 4,
            )

            def launch() -> None:
                launch_ui(
                    server.port,
                    server.view_token,
                    server.control_token,
                    config=config,
                )

            server.serve(on_started=launch)
        except (ConfigError, LedgerError, UIServerError) as error:
            Console(stderr=True).print(
                str(error), markup=False, highlight=False, soft_wrap=True
            )
            raise typer.Exit(code=1) from None

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

    bug = typer.Typer(help="Manage bugs in the Bestiary.", no_args_is_help=True)

    def bug_ledger() -> tuple[Ledger, Bugs]:
        git = GitOps(Path.cwd())
        config = load_config(git.root / ".specvania/config.toml")
        guard = RedactionGuard()
        for secret in (
            os.environ.get(config.provider.api_key_env, ""),
            os.environ.get(config.ledger.database_url_env, ""),
        ):
            if secret:
                guard.register(secret)
        ledger = Ledger.from_config(
            git.root, config.ledger, clock=SystemClock(), guard=guard
        )
        try:
            project = ledger.add_project(git.root, git.origin_url())
        except (LedgerError, GitError):
            ledger.close()
            raise
        return ledger, Bugs(ledger, project.id)

    @bug.command("add", help="Open a bug in the Bestiary.")
    def bug_add(
        title: Annotated[str, typer.Argument(help="Bug title.")],
        feature: Annotated[str | None, typer.Option("--feature")] = None,
        room: Annotated[str | None, typer.Option("--room")] = None,
    ) -> None:
        try:
            ledger, bugs = bug_ledger()
            with ledger:
                added = bugs.add(title, feature=feature, room=room)
        except (ConfigError, GitError, LedgerError, BugError) as error:
            Console(stderr=True).print(
                str(error), markup=False, highlight=False, soft_wrap=True
            )
            raise typer.Exit(code=1) from None
        Console().print(f"Opened bug {added.id}: {added.title}", markup=False)

    @bug.command("list", help="Show the Bestiary (open bugs by default).")
    def bug_list(
        all_bugs: Annotated[
            bool, typer.Option("--all", help="Include closed bugs.")
        ] = False,
    ) -> None:
        try:
            ledger, bugs = bug_ledger()
            with ledger:
                rows = bugs.list(include_closed=all_bugs)
                features = {f.id: f.slug for f in ledger.list_features(bugs.project_id)}
        except (ConfigError, GitError, LedgerError, BugError) as error:
            Console(stderr=True).print(
                str(error), markup=False, highlight=False, soft_wrap=True
            )
            raise typer.Exit(code=1) from None
        table = Table(title="Bestiary")
        for column in ("Bug", "Title", "Feature", "Room", "Status"):
            table.add_column(column, overflow="fold")
        for row in rows:
            table.add_row(
                Text(row.id),
                Text(row.title),
                Text(features.get(row.feature_id or "", "—")),
                Text(row.room_id or "—"),
                Text(row.status),
            )
        Console(markup=False).print(table)

    @bug.command("close", help="Close a bug without a run.")
    def bug_close(
        bug_id: Annotated[str, typer.Argument(help="Bug id.")],
        wontfix: Annotated[bool, typer.Option("--wontfix")] = False,
    ) -> None:
        try:
            if not wontfix:
                raise BugError(
                    "Use --wontfix; fixed bugs close after a fully cleared run."
                )
            ledger, bugs = bug_ledger()
            with ledger:
                closed = bugs.close_wontfix(bug_id)
        except (ConfigError, GitError, LedgerError, BugError) as error:
            Console(stderr=True).print(
                str(error), markup=False, highlight=False, soft_wrap=True
            )
            raise typer.Exit(code=1) from None
        Console().print(f"Closed bug {closed.id}: wontfix", markup=False)

    app.add_typer(bug, name="bug")
    return app


def main() -> None:
    """Installed entry point and Python module entry point."""
    create_app()(prog_name="specvania")
