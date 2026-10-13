"""Long-lived studio, with synchronous planner/engine workers and WS responders."""

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Condition, RLock, Thread
from threading import Event as ThreadEvent

from specvania.agent.approval import DesktopResponder
from specvania.agent.gates import GateError
from specvania.agent.sandbox import Sandbox, SandboxRefusal
from specvania.bugs import BugError, open_bug_ledger
from specvania.clock import Clock
from specvania.config import (
    Config,
    ConfigError,
    Language,
    UIConfig,
    initialize_config,
    load_config,
)
from specvania.ledger import Ledger, LedgerError
from specvania.provider.client import (
    ChatMessage,
    Completion,
    OpenAIProvider,
    Provider,
    ProviderError,
    probe_party,
)
from specvania.run.bus import EventBus
from specvania.run.engine import Engine, EngineError, PauseDecision, rebuild_run_state
from specvania.run.events import (
    ApprovalRequested,
    Event,
    RedactionGuard,
    RunPaused,
    SecretLeakError,
    WorldPayload,
    build_world_payload,
)
from specvania.run.gitops import GitError, GitOps
from specvania.run.state import RunState, fold_events
from specvania.sdd.lint import WorldLintError, validate_directory
from specvania.sdd.planner import PlanError, Question, plan
from specvania.sdd.world import WorldParseError, parse_world
from specvania.ui.server import (
    ClarifyPrompt,
    ClarifyReply,
    ControlError,
    InitProject,
    OpenProject,
    PauseReply,
    ProbeParty,
    ProbeResult,
    SaveWorld,
    SetLanguage,
    Shutdown,
    StartPlan,
    StartRun,
    StateMessage,
    StudioControl,
    StudioPhase,
    StudioProject,
    StudioState,
    UIServer,
)


class StudioError(ControlError):
    """The studio cannot perform this transition or finish its active run."""


class _RunProvider:
    """Cancellation reaches the engine's existing provider-error/abort path."""

    def __init__(self, provider: Provider, cancelled: ThreadEvent) -> None:
        self.provider, self.cancelled = provider, cancelled

    def complete(self, model: str, messages: Sequence[ChatMessage]) -> Completion:
        if self.cancelled.is_set():
            raise ProviderError(status=400)
        result = self.provider.complete(model, messages)
        if self.cancelled.is_set():
            raise ProviderError(status=400)
        return result

    def list_models(self) -> tuple[str, ...]:
        return self.provider.list_models()


class _StudioResponder(DesktopResponder):
    def __init__(self, cancelled: ThreadEvent) -> None:
        super().__init__()
        self.cancelled = cancelled

    def poll(self) -> None:
        if self.cancelled.is_set() and (pending := self.pending) is not None:
            self.answer(pending.request_id, False)


class Studio:
    def __init__(self, *, guard: RedactionGuard, clock: Clock, port: int = 0) -> None:
        self.phase: StudioPhase = "idle"
        self.stopped = ThreadEvent()
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._cancelled = ThreadEvent()
        self._worker: Thread | None = None
        self._job: Thread | None = None
        self._abort: Callable[[], None] | None = None
        self._run_state: Callable[[], RunState] = RunState
        self._project: StudioProject | None = None
        self._code_world: Path | None = None
        self._worlds: tuple[str, ...] = ()
        self._world: WorldPayload | None = None
        self._world_slug: str | None = None
        self._open_bugs = 0
        self._question: ClarifyPrompt | None = None
        self._answer: str | None = None
        self._pause: RunPaused | None = None
        self._choice: PauseDecision | None = None
        self._error: str | None = None
        self._language: Language = "en"
        self._language_override: Language | None = None
        self._active_ui: UIConfig | None = None
        self.guard, self.clock = guard, clock
        self.server = UIServer(
            guard=guard,
            clock=clock,
            port=port,
            bus=EventBus(guard),
            studio_snapshot=self.snapshot,
            studio_info=self.info,
            control_responder=self.control,
            code_repository=self.code_repository,
        )
        self.server.desktop_responder = _StudioResponder(self._cancelled)

    def code_repository(self) -> tuple[Path | None, Path | None]:
        """The code view follows the studio's open project and selected world."""
        with self._lock:
            return (
                Path(self._project.path) if self._project else None,
                self._code_world,
            )

    def snapshot(self) -> StateMessage:
        with self._lock:
            message = StateMessage(
                phase=self.phase,
                state=self._run_state(),
                approval_request=self.server.desktop_responder.pending,
            )
            self._safe(message.model_dump_json())
            return message

    def info(self) -> StudioState | None:
        with self._lock:
            if self._project is None and self._language_override is None:
                return None
            message = StudioState(
                phase=self.phase,
                project=self._project,
                language=self._language,
                worlds=self._worlds,
                world=self._world if self.phase == "world_ready" else None,
                world_slug=self._world_slug if self.phase == "world_ready" else None,
                open_bugs=self._open_bugs,
                clarify_request=self._question,
                pause_request=self._pause.reason if self._pause else None,
                error=self._error,
            )
            self._safe(message.model_dump_json())
            return message

    def _safe(self, text: str) -> None:
        # Reuse the existing guard without storing the transient payload anywhere.
        self.guard.validate(
            ApprovalRequested(
                ts=self.clock.now(),
                run_id="",
                seq=0,
                request_id="studio",
                command="",
                cwd="",
            ).model_copy(update={"command": text})
        )

    def _error_message(self, error: Exception, fallback: str) -> str:
        if isinstance(
            error,
            (
                ConfigError,
                GitError,
                LedgerError,
                WorldParseError,
                WorldLintError,
                PlanError,
                ProviderError,  # Includes ChainExhaustedError.
                EngineError,
                GateError,
                SandboxRefusal,
                BugError,
                StudioError,
            ),
        ):
            message = str(error)
            try:
                # Check the full message before truncating, including its tail.
                self._safe(message)
            except SecretLeakError:
                return fallback
            return message[:200]
        return fallback

    def _refresh(self) -> None:
        assert self._project is not None
        root = Path(self._project.path)
        self._project = StudioProject(
            path=str(root), initialized=(root / ".specvania").is_dir()
        )
        worlds: list[str] = []
        specs = root / ".specvania/specs"
        if specs.is_dir():
            for directory in sorted(specs.iterdir()):
                try:
                    self._world_directory(directory.name)
                except (StudioError, WorldLintError, WorldParseError, SandboxRefusal):
                    continue
                if directory.is_dir():
                    worlds.append(directory.name)
        self._worlds = tuple(worlds)
        self._open_bugs = 0
        if self._project.initialized:
            if self._active_ui is None:
                self._language = (
                    self._language_override
                    or load_config(root / ".specvania/config.toml").ui.language
                )
            ledger, bugs = open_bug_ledger(root, clock=self.clock, guard=self.guard)
            with ledger:
                self._open_bugs = len(bugs.list())
        elif self._language_override is None:
            self._language = "en"
        self._safe(self._project.model_dump_json())
        self._safe("\n".join(self._worlds))

    def _world_directory(self, slug: str) -> Path:
        assert self._project is not None
        # Resolve every file too: a symlinked spec must not escape the repository.
        sandbox = Sandbox(Path(self._project.path))
        directory = sandbox.resolve(f".specvania/specs/{slug}")
        for name in ("spec.md", "tasks.md"):
            sandbox.resolve(f".specvania/specs/{slug}/{name}")
        validate_directory(directory)
        return directory

    def _load_world(self, slug: str) -> None:
        directory = self._world_directory(slug)
        payload = build_world_payload(parse_world(directory))
        self._safe(payload.model_dump_json())
        with self._lock:
            self._world, self._world_slug = payload, slug

    def transition(self, phase: StudioPhase) -> None:
        with self._lock:
            next_phase: dict[StudioPhase, tuple[StudioPhase, ...]] = {
                "idle": ("project_open",),
                "project_open": ("planning", "world_ready"),
                "planning": ("world_ready", "project_open"),
                "world_ready": ("running", "project_open"),
                "running": ("finished",),
                "finished": ("project_open",),
            }
            if phase not in next_phase[self.phase]:
                raise StudioError("Studio message does not fit the current phase.")
            self.phase = phase
            if phase == "project_open":
                self._world, self._world_slug = None, None
                self.server.run_id = ""
                self._run_state = RunState
            self.server.publish_studio_state()

    def attach_active_run(
        self,
        *,
        worker: Thread,
        abort: Callable[[], None],
        run_id: str,
        state: Callable[[], RunState],
    ) -> None:
        """Cancellation uses the engine's normal abort path and event allocator."""
        with self._lock:
            if self.phase != "world_ready" or self._worker is not None:
                raise StudioError("Studio cannot attach another active run.")
            self._worker, self._abort, self._run_state = worker, abort, state
            self.server.run_id = run_id
            self.transition("running")

    def _cancel(self) -> None:
        with self._condition:
            self._cancelled.set()
            self._condition.notify_all()

    def _clarify(self, question: Question) -> str:
        prompt = ClarifyPrompt(
            question_id=question.id[:200],
            text=question.question[:200],
            options=tuple(option[:200] for option in question.options or ()),
            default=question.default[:200],
        )
        self._safe(prompt.model_dump_json())
        with self._condition:
            self._question, self._answer = prompt, None
            self.server.publish_studio_state()
            self._condition.wait_for(
                lambda: self._answer is not None or self._cancelled.is_set()
            )
            if self._cancelled.is_set():
                raise PlanError("Studio plan cancelled.")
            assert self._answer is not None
            return self._answer

    def _decide(self, pause: RunPaused) -> PauseDecision:
        with self._condition:
            if self._cancelled.is_set():
                return "abort"
            self._pause, self._choice = pause, None
            if self._project is not None:
                self._refresh()
            self.server.publish_studio_state()
            self._condition.wait_for(
                lambda: self._choice is not None or self._cancelled.is_set()
            )
            choice = "abort" if self._cancelled.is_set() else self._choice
            self._pause, self._choice = None, None
            assert choice is not None
            return choice

    def _settings(self) -> tuple[Path, Config, str]:
        assert self._project is not None
        if not self._project.initialized:
            raise StudioError("Initialize this project before planning or running.")
        root = Path(self._project.path)
        config = load_config(root / ".specvania/config.toml")
        if self._language_override is not None:
            config.ui.language = self._language_override
        key = os.environ.get(config.provider.api_key_env, "")
        if not key.strip():
            raise StudioError(
                "API key missing; check the configured environment variable."
            )
        self.guard.register(key)
        if url := os.environ.get(config.ledger.database_url_env, ""):
            self.guard.register(url)
        return root, config, key

    def _start(self, message: StartPlan | StartRun) -> None:
        root, config, key = self._settings()
        directory: Path | None = None
        if isinstance(message, StartRun):
            if message.token_budget is not None:
                config.limits.token_budget = message.token_budget
            directory = self._world_directory(message.slug)
            git = GitOps(root)
            git.head()
            dirty = git.dirty_paths()
            self._safe("\n".join(dirty))
            if dirty and not message.allow_dirty:
                raise StudioError(
                    "Git tree is dirty; cancel or start with allow_dirty.",
                    dirty_paths=dirty,
                )
            self._code_world = directory
        elif not message.idea.strip():
            raise StudioError("Give the Mage a non-empty idea.")
        self._cancelled.clear()
        self._error = None
        self._active_ui = config.ui
        self._language = config.ui.language
        if isinstance(message, StartPlan):
            self._code_world = None
            self.transition("planning")

        def execute() -> None:
            final_state = RunState()
            try:
                with OpenAIProvider(config.provider, api_key=key) as provider:
                    with Ledger.from_config(
                        root, config.ledger, clock=self.clock, guard=self.guard
                    ) as ledger:
                        assert self.server.bus is not None
                        if isinstance(message, StartPlan):

                            def started(
                                run_id: str, snapshot: Callable[[], Sequence[Event]]
                            ) -> None:
                                with self._lock:
                                    self.server.run_id = run_id
                                    self._run_state = lambda: fold_events(snapshot())
                                    self.server.publish_studio_state()

                            result = plan(
                                root=root,
                                idea=message.idea,
                                config=config,
                                provider=provider,
                                ledger=ledger,
                                clock=self.clock,
                                guard=self.guard,
                                answer=self._clarify,
                                no_clarify=message.no_clarify,
                                bus=self.server.bus,
                                on_started=started,
                            )
                            self._load_world(result.directory.name)
                            final_state = self._run_state()
                        else:
                            assert directory is not None
                            engine = Engine(
                                root=root,
                                config=config,
                                provider=_RunProvider(provider, self._cancelled),
                                ledger=ledger,
                                bus=self.server.bus,
                                clock=self.clock,
                                responders=[self.server.desktop_responder],
                                decide=self._decide,
                            )

                            def run_started(run_id: str) -> None:
                                self.attach_active_run(
                                    worker=worker,
                                    abort=self._cancel,
                                    run_id=run_id,
                                    state=lambda: rebuild_run_state(ledger, run_id),
                                )

                            final_state = engine.run(
                                directory,
                                allow_dirty=message.allow_dirty,
                                on_started=run_started,
                            )
                        # Detach the projection while the ledger is still open.
                        with self._lock:
                            self._run_state = lambda: final_state
            except Exception as error:
                self._error = self._error_message(
                    error,
                    "Cannot complete the plan or run; "
                    "check project configuration and files.",
                )
            finally:
                with self._lock:
                    self._active_ui = None
                    self._run_state = lambda: final_state
                    self._question, self._answer, self._pause, self._choice = (
                        None,
                        None,
                        None,
                        None,
                    )
                    self._worker, self._abort, self._job = None, None, None
                    try:
                        self._refresh()
                    except (
                        ConfigError,
                        LedgerError,
                        GitError,
                        OSError,
                        SecretLeakError,
                    ) as error:
                        self._error = self._error_message(
                            error,
                            "Cannot refresh the project; "
                            "check configuration and ledger.",
                        )
                    if self.phase == "planning":
                        self.transition(
                            "project_open" if self._error else "world_ready"
                        )
                    elif self.phase == "running":
                        self.transition("finished")
                    else:
                        self.server.publish_studio_state()

        worker = Thread(target=execute, name="specvania-studio-worker", daemon=True)
        self._job = worker
        worker.start()

    def control(self, message: StudioControl) -> bool | ProbeResult:
        if isinstance(message, Shutdown):
            with self._lock:
                worker, abort = self._worker or self._job, self._abort or self._cancel
            if worker is not None and worker.is_alive():
                abort()
                worker.join()
                if self.phase == "running" and self._run_state().status not in (
                    "aborted",
                    "finished",
                ):
                    raise StudioError("The active run did not finish as aborted.")
            with self._lock:
                self._worker, self._abort = None, None
                if self.phase == "running":
                    self.transition("finished")
                self.stopped.set()
                self.server.stop()
            return True
        with self._condition:
            try:
                result = self._control(message)
            except Exception as error:
                raise StudioError(
                    self._error_message(
                        error,
                        "Cannot perform this operation; "
                        "check the git project, configuration and world files.",
                    ),
                    dirty_paths=error.dirty_paths
                    if isinstance(error, StudioError)
                    else (),
                ) from None
        return result if result is not None else True

    def _control(self, message: StudioControl) -> ProbeResult | None:
        if isinstance(message, SetLanguage) and not self.stopped.is_set():
            self._language_override = self._language = message.language
            if self._active_ui is not None:
                self._active_ui.language = message.language
            self.server.publish_studio_info()
            return None
        phases: dict[str, tuple[StudioPhase, ...]] = {
            "open_project": ("idle", "project_open", "world_ready", "finished"),
            "init_project": ("project_open",),
            "start_plan": ("project_open",),
            "clarify_answer": ("planning",),
            "save_world": ("project_open", "world_ready"),
            "start_run": ("world_ready",),
            "pause_decision": ("running",),
            "probe_party": ("project_open", "world_ready", "finished"),
        }
        if self.stopped.is_set() or self.phase not in phases.get(message.kind, ()):
            raise StudioError("Studio message does not fit the current phase.")
        if self._job is not None and not isinstance(
            message, (ClarifyReply, PauseReply)
        ):
            raise StudioError("The studio already has an active plan or run.")
        if isinstance(message, OpenProject):
            try:
                root = GitOps(Path(message.path).resolve()).root.resolve()
            except (GitError, OSError):
                raise StudioError("Choose a folder inside a git work tree.") from None
            self._safe(str(root))
            previous = self._project, self._worlds, self._open_bugs
            self._project = StudioProject(
                path=str(root), initialized=(root / ".specvania").is_dir()
            )
            try:
                self._refresh()
            except Exception:
                self._project, self._worlds, self._open_bugs = previous
                raise
            self._error = None
            self._world, self._world_slug = None, None
            self._code_world = None
            if self.phase != "project_open":
                self.transition("project_open")
            else:
                self.server.run_id, self._run_state = "", RunState
                self.server.publish_studio_state()
        elif isinstance(message, InitProject):
            assert self._project is not None
            initialize_config(Path(self._project.path))
            self._refresh()
            self.server.publish_studio_state()
        elif isinstance(message, (StartPlan, StartRun)):
            self._start(message)
        elif isinstance(message, ClarifyReply):
            if (
                self._question is None
                or message.question_id != self._question.question_id
            ):
                raise StudioError("Clarify question unavailable.")
            self._safe(message.answer)
            self._answer, self._question = message.answer, None
            self.server.publish_studio_state()
            self._condition.notify_all()
        elif isinstance(message, SaveWorld):
            directory = self._world_directory(message.slug)
            assert self._project is not None
            if directory.name not in self._worlds:
                raise StudioError("World unavailable.")
            payload = build_world_payload(parse_world(directory))
            self._safe(payload.model_dump_json())
            GitOps(Path(self._project.path)).save_world(message.slug)
            self._world, self._world_slug = payload, message.slug
            self._code_world = directory
            self._refresh()
            if self.phase == "project_open":
                self.transition("world_ready")
            else:
                self.server.publish_studio_state()
        elif isinstance(message, PauseReply):
            if self._pause is None or self._choice is not None:
                raise StudioError("Paused run unavailable.")
            if self._pause.reason == "stamina exhausted" and message.choice == "skip":
                raise StudioError("Choose retry or abort when stamina is exhausted.")
            self._choice, self._pause = message.choice, None
            self.server.publish_studio_state()
            self._condition.notify_all()
        elif isinstance(message, ProbeParty):
            _, config, key = self._settings()
            with OpenAIProvider(config.provider, api_key=key) as provider:
                result = ProbeResult(results=probe_party(config, provider, self.clock))
            self._safe(result.model_dump_json())
            return result
        return None

    def serve(self, *, on_started: Callable[[], None]) -> None:
        self.server.serve(on_started=on_started)
