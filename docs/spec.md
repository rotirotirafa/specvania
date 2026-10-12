# specvania — Spec v0.2

> A spec-driven coding agent where **your spec becomes a metroidvania map**.
> A party of open models (served by NVIDIA NIM by default) plans, codes and reviews your work, and you watch it explore, room by room — in your browser or in a desktop game window.

| | |
|---|---|
| Status | Draft — waiting for owner review |
| Owner | @rotirotirafa |
| Date | 2026-10-12 |
| Read with | [`docs/domain.md`](domain.md) · [`docs/conventions.md`](conventions.md) · [`docs/tasks.md`](tasks.md) · [`.agent/execution-protocol.md`](../.agent/execution-protocol.md) |

**Changelog**
- v0.2 (2026-10-12) — Added the ledger (runs, features, spec versions, bugs) with SQLite by default and Postgres as a pluggable adapter; bugs become monsters on the map; Electron desktop window next to the browser; shell approvals also from the desktop window. Redis considered and left out (D14).
- v0.1 (2026-10-12) — First draft: CLI, team of NIM models, SDD world format, metroidvania map in the browser.

---

## 1. Context

Coding agents (Devin, Claude Code, Aider, OpenHands, OpenCode) run as a single model in a loop, and their progress lives in a scrolling log. Three things are missing:

1. **The spec is not the backbone.** Most agents take a prompt, not a contract. Spec-Driven Development (SDD) fixes this: a model plans and writes a verifiable spec, a cheaper model executes one task at a time, and a reviewer checks the result against the acceptance criteria.
2. **Progress is invisible.** Pixel-art "agent office" visualizers exist (Pixel Agents, Claude Office, OpenCode Visualizer, pixtuoid), but they show *who is busy*, not *where the work is*. None of them is driven by a spec.
3. **Nothing is remembered.** Each agent session starts from zero: which features were built, from which version of the spec, what failed, which bugs are still open.

NVIDIA's API catalog (build.nvidia.com) exposes 100+ open models behind one OpenAI-compatible endpoint, free for prototyping at ~40 requests/minute. That makes a **multi-model team** cheap to build and easy to swap — as long as the agent respects the rate limit and can fall back between models.

specvania combines it all: **SDD-native workflow + a team of open models + a project ledger + a metroidvania map that is a live projection of the spec.**

## 2. Goals

- G1. Turn an idea into a verifiable spec (`spec.md` + `tasks.md`) through a Clarify step, or accept a spec the user wrote.
- G2. Execute tasks one at a time, in dependency order, with a reviewer gate after each task and an area-level acceptance check.
- G3. Use different models per role (plan / code / review) with ordered fallback chains, a shared rate limiter and transparent per-model metrics.
- G4. Render the run as a metroidvania map, live or as a replay, in the browser or in a desktop window, with no network access at runtime.
- G5. Produce a run summary (calls, tokens, 429s, fallbacks, durations, optional cost-equivalent) that is good enough to publish.
- G6. Keep a ledger of the project's history — runs, features, spec versions, events and bugs — that works with zero setup and can move to Postgres for team use.

## 3. Non-goals (v0.2)

- IDE integration, Docker sandbox for command execution, parallel task execution.
- Redis or any message broker (D14).
- A hosted server, user accounts or authentication beyond local tokens (D16).
- Pushing branches or opening PRs (specvania commits locally, never pushes).
- Native function-calling (D3), multiplayer, approvals from a plain browser tab.
- Long-context summarization of agent history (truncation only).
- Signed and notarized desktop builds; Windows as a first-class platform (best effort).

## 4. Clarify log

| # | Question | Answer |
|---|---|---|
| C1 | Product shape | Terminal CLI (Devin / Claude Code style) |
| C2 | Differentiator | Team of NIM models: planner / coder / reviewer, fallback on 429, cost transparency |
| C3 | Language of the codebase | Python |
| C4 | Workflow | SDD-native, with an organized visual layer |
| C5 | Visual direction | Metroidvania map (not the "pixel office" metaphor already in use) |
| C6 | Shell execution | Local, every model-proposed command needs explicit approval |
| C7 | Who writes the spec | The planner generates it after Clarify; a hand-written spec is also accepted |
| C8 | Repo language | No preference → English (code, docs, README) |
| C9 | Sessions and history (features, specs, bugs) | Ledger with SQLite by default, Postgres pluggable via `docker compose`; no Redis |
| C10 | Visual shell | Electron desktop window **and** browser, both loading the same web app |
| C11 | Where approvals happen | Terminal and desktop window; first answer wins |

## 5. How SDD maps to the game

| SDD concept | specvania | On the map |
|---|---|---|
| Spec (`spec.md` + `tasks.md`) | World | The whole map |
| Task group / milestone | Area | A region with its own color |
| Task | Room | A room, locked until its doors open |
| `depends_on` | Door | A door between rooms; opens when the source room is cleared |
| Task checklist passes review | Room cleared | Room lights up, a commit is made |
| Scenarios assigned to an area | Boss | Boss fight at the end of the area; one HP segment per scenario |
| Planner / Coder / Reviewer | Mage / Warrior / Paladin | The party |
| Rate-limit budget | Stamina | Bar under each character |
| 429 backoff | Cooldown | Character rests, timer shown |
| Reviewer rejects | Respawn | Warrior returns to the room entrance with feedback |
| Max attempts per room | Lives | Hearts in the HUD |
| Cost-equivalent | Coins | Counter per character |
| Open bug | Monster | A monster in the room where the bug was found; listed in the Bestiary |
| Project history | Ledger | The save file |

Full glossary and naming rules: [`docs/domain.md`](domain.md).

## 6. User-facing surface (v0.2)

```
specvania init                 # create .specvania/config.toml in the current git repo
specvania doctor               # check API key, models, ledger connection
specvania plan "<idea>" [--ui[=auto|browser|desktop]] [--no-clarify] [--from-bug <id>]
                               # Mage: Clarify (0–5 questions) → spec.md + tasks.md
specvania lint <spec-dir>      # validate a world (format, doors, coverage)
specvania run <spec-dir> [--ui[=auto|browser|desktop]] [--allow-dirty]
                               # execute rooms in order; review; bosses; commits per room
specvania replay <run-id|path/to/events.jsonl|demo> [--speed 1|2|4]
                               # play a recorded run in the map, no API key needed
specvania status [<run-id>]    # rooms, attempts, per-model metrics of a run
specvania history [<feature>]  # features, spec versions, runs, open bugs
specvania bug add "<title>" [--feature <slug>] [--room <Txx>]
specvania bug list [--all]     # open bugs (the Bestiary)
specvania bug close <id> --wontfix
specvania export <run-id>      # write the run's events as events.jsonl
```

Files written in the user's repository:

```
.specvania/
  config.toml                  # provider, roles → model chains, limits, gates, ledger, ui
  pricing.toml                 # optional, user-maintained reference prices
  ledger.db                    # SQLite ledger (default; gitignored)
  specs/<slug>/spec.md         # versioned in git by the user
  specs/<slug>/tasks.md
  runs/<run-id>/summary.md     # gitignored
  runs/<run-id>/events.jsonl   # only when exported
```

Repository extras: `docker-compose.yml` (Postgres only, optional) and `apps/desktop/` (Electron shell).

## 7. World format v0 (what `lint` enforces)

specvania's own `docs/spec.md` and `docs/tasks.md` follow this format (dogfooding).

**spec.md** — free markdown, except scenarios, which must be declared as:

````markdown
### S1 · Short title
```gherkin
Given ...
When ...
Then ...
```
````

**tasks.md** — areas and rooms:

```markdown
## A1 · Area title
boss: S1, S3

### T01 · Room title
depends_on: none
covers: S1
files: path/a.py, path/b.py

Free-text context for the executor.

Checklist:
- [ ] Binary, verifiable item
```

- `boss:` scenario ids fought at the end of the area, or `none`.
- `depends_on:` room ids whose doors lead here, or `none`.
- `covers:` scenario ids this room contributes to, or `none`.
- `files:` files or folders the room is expected to touch (comma-separated).
- Lines are exact: no inline comments; headings inside fenced code blocks are ignored.

Lint rules:

- L1. Area ids are `A<n>`, room ids are `T<nn>`, scenario ids are `S<n>`; all unique.
- L2. Every `depends_on` id exists and belongs to the same area or an earlier one; the door graph has no cycles.
- L3. Every room has `depends_on`, `covers`, `files` and a non-empty `Checklist:`.
- L4. Every scenario in `spec.md` is covered by at least one room and listed as the boss of exactly one area.
- L5. A boss scenario is covered by at least one room in its own area or an earlier one.
- L6. Errors report file, line and rule id; exit code is non-zero on any error.

## 8. Architecture at a glance

```
            ┌──────────── specvania (Python process) ────────────┐
 terminal ◄─┤ CLI ─ Mage / Warrior / Paladin ─ Router ─ Limiter ─┼──► OpenAI-compatible API (NIM)
 (y/N)      │        │                                            │
            │      Engine ──► Event bus ──► Ledger (SQLite | Postgres)
            │        │            │                               │
            │     git (branch,    └──► UI server (FastAPI, 127.0.0.1)
            │      commits)              │  WebSocket + static web app
            └────────────────────────────┼───────────────────────────┘
                                         ├──► Browser tab      (view token: watch)
                                         └──► Electron window  (view + control token: watch, approve, notify)
```

## 9. Decisions

| Id | Decision | Why |
|---|---|---|
| D1 | Python ≥ 3.12, `uv`, Typer, Rich, Pydantic v2, `openai` SDK, FastAPI + uvicorn, SQLAlchemy 2 (Core) + Alembic; `psycopg` 3 only in the `postgres` extra | Owner's core stack; one data layer for both databases |
| D2 | Any OpenAI-compatible endpoint; default `base_url` is `https://integrate.api.nvidia.com/v1`; key read from an env var (default `NVIDIA_API_KEY`) | NIM is the default, not a lock-in; Ollama, OpenRouter etc. work by changing config |
| D3 | Model-agnostic **JSON action protocol**: each Warrior turn returns exactly one JSON action; parsed and validated with Pydantic; up to 2 repair retries on invalid output | Catalog models differ in native tool-calling support; one protocol works for every model and is easy to log and render. Native tool-calling is future work |
| D4 | Three roles with ordered fallback chains in `config.toml`. Initial defaults (snapshot of `/v1/models` on 2026-10-12, **quality not yet verified**): Mage `moonshotai/kimi-k3` → `z-ai/glm-5.3` → `nvidia/nemotron-3-ultra-550b-a55b`; Warrior `z-ai/glm-5.3` → `moonshotai/kimi-k2.6` → `deepseek-ai/deepseek-v4.1-flash`; Paladin `nvidia/nemotron-3-ultra-550b-a55b` → `deepseek-ai/deepseek-v4.1-flash` → `z-ai/glm-5.3-flash` | Strong reasoning to plan, strong agentic coder to execute, a different family to review (avoids a model grading itself). T05 verifies ids; T23 validates quality |
| D5 | One token-bucket limiter per API key, shared by all roles (default 40 RPM). 429 / 5xx → exponential backoff with jitter, honoring `Retry-After`. After 3 consecutive failures on a model, the role moves to the next model in its chain for the rest of the room | The free tier limit is per key, not per model |
| D6 | **Event sourcing**: every state change is an immutable event appended to the ledger's `events` table, the run's source of truth. Run state, the terminal view, the map and `summary.md` are projections. `export` writes `events.jsonl`; `replay` accepts a run id or a `.jsonl` file | One source of truth; live and replay share the same code path; runs stay portable and shareable |
| D7 | `run` requires a git repo and a clean tree (unless `--allow-dirty`), creates branch `specvania/<run-id>` from `HEAD`, makes one commit per cleared room, never pushes | Every room is a rollback point; nothing leaves the machine |
| D8 | Model-proposed shell commands always need approval, in the terminal **or** the desktop window; the first answer wins and the other side shows how it was resolved. Gate commands written by the user in `config.toml` run without a prompt | Owner decisions C6 and C11; gates are the user's own instructions, not model output |
| D9 | UI is one web app (vanilla ES modules + vendored Phaser 3, pinned, MIT) served by FastAPI with one WebSocket. Two shells load it: a browser tab and the Electron app in `apps/desktop/` (plain JS main and preload, no bundler, `electron-builder` for packaging). `--ui=auto` prefers the desktop app when installed and falls back to the browser | No duplicated UI code; the Python package works without Node; the desktop adds a game window, native notifications and approvals |
| D10 | Art is original: sprites defined in code as palette + pixel strings and rendered to textures at startup. Pixel font vendored under OFL. No characters, tiles or names from existing games | Open-source safe; diffable; no binary asset pipeline |
| D11 | No prices are shipped. `pricing.toml` is user-maintained, each entry with source URL and date. Summary always shows tokens; coins only appear for models with a configured price | Invented numbers would undermine trust in the published results |
| D12 | Files sent to models skip `.git/`, ignored paths and secret patterns (`.env*`, `*.pem`, `*.key`, `id_*`); file tools cannot read or write outside the repo root | Basic safety for a local agent |
| D13 | Limits: max 25 Warrior steps per attempt, 3 lives per room, observations truncated to 8 kB each | Bounded cost per room; all configurable |
| D14 | **Ledger behind one storage port.** SQLite at `.specvania/ledger.db` by default; Postgres when `SPECVANIA_DATABASE_URL` is set. Same SQLAlchemy Core schema and Alembic migrations for both, applied automatically on start. Tables: `projects`, `features`, `spec_versions`, `runs`, `events`, `bugs`. **No Redis**: within one process the event bus is in memory; cross-process notification, if ever needed, uses Postgres `LISTEN/NOTIFY` | Zero setup for the first run; team mode without code changes; one service fewer to install and explain |
| D15 | `docker-compose.yml` provides Postgres only, for team mode and for the Postgres test job. Docker is never required to use specvania | Keeps `uvx specvania` working on a clean machine |
| D16 | UI server binds to `127.0.0.1` and issues two random tokens per process: a **view token** (required to load the page and open the WebSocket) and a **control token** (required to answer approvals), passed only to the desktop app at launch. The WebSocket checks the `Origin` header. A browser tab gets the view token only | Any web page can try to reach localhost; approving shell commands must require a secret only the desktop app holds |
| D17 | **Bugs** come from `bug add`, from a room that runs out of lives and is skipped, and from a failed boss scenario. Lifecycle: `open` → `fixed` (closed automatically when a run planned with `--from-bug` clears) or `wontfix`. Open bugs render as monsters in their room and in the Bestiary | Failures become tracked work instead of lost log lines |

## 10. Acceptance — business scenarios (BDD)

### S1 · Plan with Clarify
```gherkin
Given a git repository and an idea with open decisions
When I run specvania plan "add an invoices endpoint with PDF export"
Then the Mage asks between 1 and 5 questions in the terminal, each with a default
And pressing Enter accepts the default
And .specvania/specs/<slug>/spec.md and tasks.md are written
And specvania lint passes on that folder
And the ledger records the feature and its first spec version
```

### S2 · Plan without Clarify
```gherkin
Given an idea with no open decisions, or the flag --no-clarify
When I run specvania plan
Then no question is asked
And a world that passes lint is written
```

### S3 · Bring your own spec
```gherkin
Given a hand-written spec.md and tasks.md
When I run specvania lint on that folder
Then I get "OK" or a list of errors with file, line and rule id
And specvania run accepts any folder that passes lint
```

### S4 · Rooms in door order, one commit per room
```gherkin
Given a world where T02 depends on T01
When I run specvania run
Then T02 starts only after T01 is cleared
And the run works on branch specvania/<run-id>
And each cleared room produces exactly one commit referencing its room id
And nothing is pushed
```

### S5 · Shell approval in the terminal
```gherkin
Given the Warrior proposes the shell command "pytest -q"
Then the terminal shows the command and its working directory and waits for y/N
When I answer N
Then the command does not run
And the Warrior receives "denied by user" as the observation and continues
```

### S6 · Repository boundary and secrets
```gherkin
Given the Warrior asks to read ../outside.txt or .env
Then the action is refused without touching the file
And a "tool_refused" event is recorded with the reason
```

### S7 · Review, respawn and lives
```gherkin
Given the Paladin rejects a room
Then the Warrior respawns in the same room with the Paladin's feedback
And the attempt counter increases
When the room runs out of lives
Then the run pauses and asks: retry, skip or abort
```

### S8 · Rate limit and fallback
```gherkin
Given the provider answers 429 with Retry-After: 5
Then the role waits at least 5 seconds and a "cooldown" event is recorded
When the same model fails 3 times in a row
Then the role switches to the next model in its chain and a "fallback" event is recorded
And the total request rate never exceeds the configured RPM
```

### S9 · Live map
```gherkin
Given I run specvania run with --ui
Then the world map opens in the desktop window or, if it is not installed, in a browser tab
And rooms change state within 1 second of the matching event
And each party member shows its current model, status, stamina and coins or tokens
```

### S10 · Replay without an API key
```gherkin
Given no API key is configured
When I run specvania replay demo
Then the map plays the bundled demo run at the chosen speed
And no network request leaves the machine
```

### S11 · Run summary
```gherkin
Given a finished, paused or aborted run
Then runs/<run-id>/summary.md lists, per model: calls, input and output tokens, 429s, fallbacks and total latency
And per room: attempts, result and commit hash
And cost-equivalent appears only for models priced in pricing.toml, with the source of each price
```

### S12 · Boss fight
```gherkin
Given all rooms of an area are cleared
Then the Paladin evaluates each boss scenario of that area against the area diff and the gate results
And the boss HP bar loses one segment per scenario that passes
When any scenario fails
Then the run pauses and reports which scenario failed and why
```

### S13 · Project history
```gherkin
Given two runs of the same feature, with the spec edited between them
When I run specvania history <feature>
Then both runs appear with status, rooms cleared, tokens and the spec version they used
And both spec versions appear with their hash and creation time
And the history survives closing and reopening specvania
```

### S14 · Bugs become monsters
```gherkin
Given a room runs out of lives and I choose skip
Then a bug is opened linked to the feature, the run and the room
And a monster appears in that room on the map and in the Bestiary
When I run specvania plan --from-bug <id> and the resulting run clears all rooms
Then the bug is closed as fixed, linked to that run
```

### S15 · Postgres team mode
```gherkin
Given Postgres is running from docker compose and SPECVANIA_DATABASE_URL points to it
When I run init, plan, run, history and replay
Then everything behaves as with SQLite
And the same ledger test suite passes on both databases
```

### S16 · Desktop window
```gherkin
Given the specvania desktop app is installed
When I run specvania run --ui
Then a desktop window opens with the map
And native notifications appear for an approval request, a rejected room and a boss result
Given the desktop app is not installed
Then a browser tab opens instead, with a one-line notice in the terminal
```

### S17 · Approve from the desktop window
```gherkin
Given the Warrior is waiting for approval of a shell command
When I approve it in the desktop window
Then the command runs
And the terminal prompt closes with "approved in desktop window"
And a browser tab showing the same run cannot approve or deny it
```

## 11. Acceptance — technical checklist

- [ ] `ruff check`, `ruff format --check`, `pyright` and `pytest` pass in CI on Linux and macOS, Python 3.12 and 3.13.
- [ ] A CI job runs the ledger tests and one end-to-end fake-provider run against Postgres from `docker-compose.yml`.
- [ ] Tests run with no external network: providers are faked; a test fails if a socket is opened to any host other than the configured test database.
- [ ] No API key value or UI token ever appears in the ledger, exported events, `summary.md` or logs (covered by a test).
- [ ] `uv build` produces a wheel that includes the UI static files and the demo; `uvx specvania replay demo` works from that wheel without Node or Docker.
- [ ] The UI loads with the network disabled (no CDN, no remote fonts), in the browser and in the desktop app.
- [ ] The Electron renderer runs with `contextIsolation: true`, `nodeIntegration: false` and `sandbox: true`.
- [ ] Every third-party file (Phaser, font) and the Electron dependencies are listed with their licenses in `THIRD_PARTY_NOTICES.md`.
- [ ] `specvania lint docs/` passes on this repository (dogfooding).

## 12. Definition of done (v0.2)

All scenarios S1–S17 pass, the technical checklist is green, the desktop app builds for macOS (arm64, x64) and Linux (AppImage) in CI, the package ships a real recorded run (`real-run`) produced by specvania on `examples/sample-todo-api/` next to the synthetic `demo`, and the README GIF comes from the real run.

## 13. Open points (decide during execution, log in `.agent/deviations.md`)

- O1. Final default models per role after T23 measures quality on the sample project.
- O2. Whether the Paladin should also review the spec itself before a run (beyond lint).
- O3. Map layout algorithm details if worlds grow beyond ~30 rooms.
- O4. How the desktop app is distributed beyond GitHub Releases (Homebrew cask, etc.).
