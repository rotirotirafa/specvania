# Conventions

Project rules for every room. Decisions with a "why" live in `docs/spec.md` §9; this file is the day-to-day "how".

## Stack

- Python ≥ 3.12, managed with `uv`. Package name `specvania`, CLI entry point `specvania`.
- Runtime dependencies allowed: `typer`, `rich`, `pydantic` (v2), `openai`, `fastapi`, `uvicorn[standard]`, `sqlalchemy` (2.x, Core only), `alembic`, `tomli-w`, `pathspec`.
- Optional extra `postgres`: `psycopg[binary]` (3.x).
- Dev dependencies allowed: `pytest`, `hypothesis`, `ruff`, `pyright`.
- Web app: vanilla ES modules, vendored Phaser 3 (pinned), vendored pixel font. No npm, no bundler, no CDN.
- Desktop app (`apps/desktop/` only): `electron` and `electron-builder` as dev dependencies; main and preload in plain JavaScript (CommonJS), no bundler, no other npm packages.
- Infra: `docker-compose.yml` with one `postgres` service. Never a runtime requirement.
- Anything else is a stop rule (S-3): ask first, then log a deviation.

## Layout

```
src/specvania/
  cli.py                 # Typer app; thin, delegates to modules
  config.py
  pricing.py
  bugs.py
  clock.py               # Clock protocol + SystemClock + FakeClock
  ledger/                # schema.py, repo.py (Ledger API), migrations/ (Alembic)
  provider/              # client.py, ratelimit.py, router.py, fake.py
  sdd/                   # world.py, lint.py, planner.py, prompts/*.md
  agent/                 # actions.py, tools.py, sandbox.py, approval.py, executor.py, reviewer.py, gates.py, prompts/*.md
  run/                   # events.py, bus.py, state.py, jsonl.py, engine.py, gitops.py, summary.py
  ui/                    # server.py, launcher.py, static/
  examples/              # demo/ (synthetic), real-run/ (recorded)
apps/desktop/            # Electron shell (package.json, main.js, preload.js, electron-builder.yml, scripts/)
tests/                   # mirrors src layout; fixtures/ for worlds and event streams
examples/                # sample projects to run specvania on (not shipped in the wheel)
docker-compose.yml       # optional Postgres for team mode and the Postgres test job
```

## Code

- Type hints everywhere; pyright in `standard` mode with zero errors.
- Pydantic models for anything that crosses a boundary: config, events, actions, model outputs, WebSocket messages.
- Events are frozen models; never mutate an event after it is emitted.
- No module-level mutable state. Pass dependencies explicitly (config, clock, bus, ledger, provider).
- Time only through `Clock` (`now()`, `sleep()`); never call `time.sleep` or `datetime.now` directly outside `clock.py`.
- Engine, agent, ledger and provider code are synchronous. Async lives only in `ui/server.py`. The bus is thread-safe; the server bridges into asyncio with `loop.call_soon_threadsafe`.
- Domain code talks to the `Ledger` API only; SQLAlchemy stays inside `ledger/`. No dialect-specific SQL outside `ledger/schema.py`.
- Every schema change is an Alembic migration that runs on both SQLite and Postgres.
- Prompts are Markdown files under `prompts/`, loaded with `importlib.resources`. No prompt text inline in Python beyond a one-line fallback.
- User-facing terminal output goes through Rich; library code does not print.
- Errors the user can fix (missing key, dirty tree, lint errors, unreachable ledger) are typed exceptions turned into one clear message and a non-zero exit code in `cli.py`. No tracebacks for expected errors.

## Tests

- Every checklist item has a test unless it is explicitly a manual check (G6/G8).
- No external network in tests. Use `provider/fake.py`. The socket guard in `tests/conftest.py` must stay on; it only allows the host of `SPECVANIA_TEST_DATABASE_URL`.
- Ledger tests run on SQLite always and on Postgres when `SPECVANIA_TEST_DATABASE_URL` is set (`@pytest.mark.pg` for Postgres-only cases).
- No real API keys; tests set a fake `NVIDIA_API_KEY` value and assert it never leaks.
- Use `FakeClock` for anything involving waits, rate limits or replay speed.
- Git tests use temporary repositories created inside `tmp_path`.

## Events

- Event `type` values are `snake_case` and listed in `run/events.py` only.
- Adding a field is fine; renaming or removing a field or type is a deviation.
- Event payloads never contain file contents, prompts, full model outputs or secrets — summaries only (max 500 characters for `thought`, 200 for other free text).

## UI

- `pixelArt: true`, integer scaling only, internal resolution 320×180.
- Every color comes from `pixel/palette.js`.
- Original art only (spec D10). Each new sprite is added to the `sprites.html` gallery.
- Respect `prefers-reduced-motion`.
- UI text uses the glossary terms (Mage / Warrior / Paladin, room, door, boss, monster, Bestiary).
- The web app must work in a browser tab; desktop-only features check `window.specvaniaDesktop` and degrade silently.
- Electron: `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`; the preload exposes only what `docs/tasks.md` lists; navigation stays on the UI server origin.

## Git

- Branch per room while building specvania: `room/T05-provider` (optional; the owner may work on `main`).
- Commit per room: `feat(T05): <room title in lowercase>`; fixes after review: `fix(T05): ...`; docs only: `docs: ...`.
- specvania itself, when running on a user's repo, commits as `specvania(Txx): <room title>` on `specvania/<run-id>`.
- Never push from automation.
