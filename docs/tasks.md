# specvania — Tasks v0.3

World for [`docs/spec.md`](spec.md). Execute with [`.agent/execution-protocol.md`](../.agent/execution-protocol.md): one room at a time, in door order, gates after every room.

This file follows the world format v0 (spec §7) — `specvania lint docs/` must pass once T06 is done.

Map overview (doors inside each area; doors from earlier areas are listed in each room):

```
A1 Foundations   T01 ─┬─ T02 ─┬─ T04 ── T05 ............... boss: S8
                      └─ T03 ─┘
A2 Spellbook     T06 ── T07 ............................... boss: S1 S2 S3
A3 Dungeon       T08 ── T09 ─┐
                 T10 ────────┴─ T11 ─┬─ T12
                                     └─ T13 ............... boss: S4 S5 S6 S7 S11 S12 S13
A4 Map           T14 ─┐
                 T15 ─┴─ T16 ─┬─ T17
                              └─ T18 ...................... boss: S9 S10 S14
A5 Desktop       T19 ─┬─ T20
                      └─ T21 .............................. boss: S16 S17
A6 Ship          T22 ...................................... boss: S15
A7 Studio        T24 ─┬─ T25 ─┬─ T27
                      │       └─ T28
                      └─ T26 .............................. boss: S18 S19
A8 Castle        T29 ── T30 ── T31 ........................ boss: S20
A9 Screens       T32 ─┬─ T33 ── T34 ── T36 ── T37
                      └─ T35 .............................. boss: S21 S22
A10 Demo         T38 ── T23 ............................... boss: S23
```

v0.3 note: T23 moved from A6 to A10 so the dogfood run happens in the game; its sample project (`examples/sample-todo-api/`) is already in place.

---

## A1 · Foundations
boss: S8

### T01 · Scaffold the project
depends_on: none
covers: none
files: pyproject.toml, src/specvania/__init__.py, src/specvania/__main__.py, src/specvania/cli.py, tests/conftest.py, tests/test_cli.py, .github/workflows/ci.yml

Create the `uv` project with the src layout from `docs/conventions.md`. The Typer app registers every command from spec §6 (`bug` as a sub-app with add / list / close); every command except `--version` and `--help` prints "not implemented yet" and exits with code 2 until its room is cleared. `tests/conftest.py` installs a socket guard so any test that opens a network connection fails, except to the host of `SPECVANIA_TEST_DATABASE_URL` when set.

Checklist:
- [x] `uv run specvania --help` lists init, doctor, plan, lint, run, replay, status, history, bug, export
- [x] `uv run specvania --version` prints the version from package metadata
- [x] A test proves the socket guard fails a test that tries to connect to a real host
- [x] CI runs ruff check, ruff format --check, pyright and pytest on ubuntu-latest and macos-latest with Python 3.12 and 3.13
- [x] All gates G1–G4 pass locally

### T02 · Config and `init`
depends_on: T01
covers: S8
files: src/specvania/config.py, src/specvania/cli.py, tests/test_config.py

Pydantic models for `.specvania/config.toml`: `provider` (base_url, api_key_env), `roles` (mage, warrior, paladin — each an ordered list of model ids), `limits` (rpm, lives, max_steps, observation_kb, failures_before_fallback), `gates` (list of shell commands), `ledger` (sqlite_path, database_url_env), `ui` (host, port, mode: auto / browser / desktop), `desktop` (optional app path). Defaults come from spec D2, D4, D5, D9, D13 and D14. `specvania init` writes the file in the current git repo and appends `.specvania/ledger.db*` and `.specvania/runs/` to `.gitignore`.

Checklist:
- [x] Defaults match D2, D4, D5, D9, D13 and D14 exactly (asserted in a test)
- [x] Unknown keys and empty role chains are rejected with a readable error
- [x] `init` outside a git repository exits non-zero without writing anything
- [x] `init` refuses to overwrite an existing config unless `--force` is given
- [x] `init` adds both ignore lines to `.gitignore` once (idempotent)
- [x] Neither the API key nor the database URL value is ever stored in the config, only env var names

### T03 · Events, bus and clock
depends_on: T01
covers: S11
files: src/specvania/clock.py, src/specvania/run/events.py, src/specvania/run/bus.py, src/specvania/run/state.py, src/specvania/run/jsonl.py, tests/test_events.py, tests/test_state.py, tests/test_jsonl.py

`clock.py`: `Clock` protocol, `SystemClock`, `FakeClock`. Events are frozen Pydantic models in a discriminated union (`type` field), each with `ts`, `run_id` and `seq`. Minimum catalog: run_started, world_loaded, clarify_question, clarify_answer, spec_written, room_entered, attempt_started, model_call, cooldown, fallback, action, tool_refused, approval_requested, approval_answered, gate_result, review, respawn, room_cleared, room_failed, room_skipped, boss_started, boss_scenario, boss_result, bug_opened, bug_closed, run_paused, run_finished. The bus is thread-safe in-process pub/sub. `state.py` folds events into run state. `jsonl.py` encodes and decodes event streams for export and replay.

Checklist:
- [x] Every event type round-trips through JSON without loss (parametrized test)
- [x] Folding the same event list twice produces identical state
- [x] A redaction guard rejects any event whose serialized form contains a registered secret (API key value, UI tokens), tested with fake secrets
- [x] Decoding a `.jsonl` stream with a truncated last line ignores that line with a warning
- [x] Bus subscribers on other threads receive events in `seq` order

### T04 · Ledger: SQLite and Postgres
depends_on: T02, T03
covers: S13, S15
files: src/specvania/ledger/, pyproject.toml, docker-compose.yml, tests/test_ledger.py, tests/conftest.py, .github/workflows/ci.yml

Implement D14 and D15. One `Ledger` API over SQLAlchemy Core: projects (repo root + origin URL), features (slug, title, status), spec_versions (feature, sha256, spec_md, tasks_md, created_at; identical content returns the existing version), runs (feature, spec version, branch, status, timestamps, totals), events (run, seq, type, ts, JSON payload — JSONB on Postgres), bugs (feature, run, room, title, body, source, status, fixed_by_run). `append_event` assigns `seq` atomically. Alembic migrations run automatically on open. SQLite uses WAL mode. `docker-compose.yml` defines one `postgres` service (official image, pinned major version, local-only credentials documented as such). `psycopg` lives in the `postgres` extra.

Checklist:
- [x] The ledger test suite is parametrized over SQLite and Postgres; the Postgres variant runs when `SPECVANIA_TEST_DATABASE_URL` is set and is skipped with a visible reason otherwise
- [x] A CI job starts Postgres from `docker-compose.yml` and runs the Postgres variant
- [x] Opening a new database applies all migrations; opening it again is a no-op
- [x] Two threads appending to the same run never produce duplicate or missing `seq` values
- [x] Adding the same spec content twice returns the same version id
- [x] Every append goes through the redaction guard from T03
- [x] `pip install specvania` without the extra works with SQLite and gives a clear message when a Postgres URL is configured

### T05 · Provider, rate limiter, router and `doctor`
depends_on: T02, T03, T04
covers: S8
files: src/specvania/provider/client.py, src/specvania/provider/ratelimit.py, src/specvania/provider/router.py, src/specvania/provider/fake.py, src/specvania/cli.py, tests/test_ratelimit.py, tests/test_router.py, tests/test_doctor.py

OpenAI-compatible client (chat completions + list models) using the `openai` SDK with `base_url` from config. One token-bucket limiter per API key shared by all roles (D5). The router resolves a role to a model, retries 429/5xx with exponential backoff and jitter honoring `Retry-After`, and moves to the next model in the chain after `failures_before_fallback` consecutive failures; the chain resets when a new room starts. Every call emits `model_call`; waits emit `cooldown`; switches emit `fallback`. `fake.py` provides a scripted provider for tests. `specvania doctor` checks the env var, lists models from `/v1/models`, marks every configured model as found or missing, and checks the ledger connection and migration version.

Checklist:
- [x] With a fake clock, 100 queued calls at rpm=40 never exceed 40 calls in any 60-second window
- [x] A 429 with `Retry-After: 5` waits at least 5 seconds (fake clock) and emits `cooldown`
- [x] 3 consecutive failures switch to the next model and emit `fallback` with from/to ids
- [x] When the whole chain fails, the router raises a typed error the engine can turn into `run_paused`
- [x] `model_call` events carry role, model, tokens in/out, latency and status
- [x] `doctor` exits non-zero when the env var is missing, a configured model is not in `/v1/models`, or the ledger is unreachable
- [x] Default model ids from D4 are checked against the real catalog once (manually, by the owner) and the result is recorded in `.agent/deviations.md`

---

## A2 · Spellbook
boss: S1, S2, S3

### T06 · World format: parser and `lint`
depends_on: T01
covers: S3
files: src/specvania/sdd/world.py, src/specvania/sdd/lint.py, src/specvania/cli.py, tests/test_world.py, tests/test_lint.py, tests/fixtures/worlds/

Parse `spec.md` scenarios and `tasks.md` areas and rooms (spec §7) into typed models (World, Area, Room, Door, Scenario) with source line numbers. Implement lint rules L1–L6. `specvania lint <dir>` prints "OK" or one line per error: `path:line rule message`.

Checklist:
- [x] Fixtures exist for a valid world and for at least one violation of each rule L1–L5
- [x] Each fixture produces the expected rule id and line number
- [x] Cycles are reported with the full cycle path (e.g. `T03 → T05 → T03`)
- [x] Headings inside fenced code blocks are ignored in both files (the format example in spec §7 and the map overview above must not create scenarios, areas or rooms)
- [x] `specvania lint docs/` on this repository prints OK

### T07 · Mage: Clarify and `plan`
depends_on: T04, T05, T06
covers: S1, S2, S13
files: src/specvania/sdd/planner.py, src/specvania/sdd/prompts/clarify.md, src/specvania/sdd/prompts/spec.md, src/specvania/cli.py, tests/test_planner.py

The Mage receives the idea, a repo map (tree truncated to 300 entries) and, when present, README, `docs/conventions.md` and `docs/domain.md`. Step 1 returns JSON `{needs_clarification, questions[{id, question, options?, default}]}` with at most 5 questions; triggers: unclear scope, missing acceptance signal, several valid architectures, destructive or irreversible change. Step 2 writes `spec.md` and `tasks.md` in world format v0. If lint fails, the errors are sent back for up to 2 repair attempts; after that `plan` exits non-zero and keeps the last draft. `--no-clarify` skips step 1. On success the ledger records the feature and the spec version.

Checklist:
- [x] With the fake provider, an ambiguous idea yields 1–5 questions and Enter accepts each default
- [x] With `--no-clarify` no question is printed
- [x] The written world passes lint; a lint failure triggers a repair call that includes the lint output
- [x] After 2 failed repairs the command exits non-zero and leaves `spec.draft.md` / `tasks.draft.md`
- [x] Clarify questions and answers are recorded as events and copied to the spec's Clarify log section
- [x] The feature and its spec version exist in the ledger after a successful `plan`
- [x] Prompts live in `prompts/*.md`, not inline in Python

---

## A3 · Dungeon
boss: S4, S5, S6, S7, S11, S12, S13

### T08 · Tools, repository boundary and approval broker
depends_on: T02, T03
covers: S5, S6
files: src/specvania/agent/tools.py, src/specvania/agent/sandbox.py, src/specvania/agent/approval.py, tests/test_tools.py, tests/test_sandbox.py, tests/test_approval.py

Tools: `read_file(path, start?, end?)`, `list_dir(path)`, `search(pattern, glob?)`, `write_file(path, content)`, `edit_file(path, old, new)`, `run_shell(command)`. Paths resolve inside the repo root (symlinks resolved), and secret patterns from D12 are refused. `run_shell` goes through an `ApprovalBroker` that accepts several responders and resolves with the first answer (D8); this room ships the terminal responder (non-blocking stdin on POSIX so it can be cancelled) and a scripted test responder. Commands run with a timeout and return exit code plus truncated output.

Checklist:
- [x] `../x`, absolute paths outside the root and symlinks escaping the root are refused and emit `tool_refused`
- [x] `.env`, `.env.local`, `*.pem`, `*.key` and `id_*` are refused for read and write
- [x] `edit_file` fails clearly when `old` matches zero or more than one time
- [x] Denying a shell command returns the observation `denied by user` and the command never runs
- [x] With two responders, the first answer wins and the other is cancelled and told who answered
- [x] Observations are truncated to `observation_kb` with a visible truncation marker
- [x] `approval_requested` and `approval_answered` (with the answering responder) are emitted

### T09 · Warrior: JSON action protocol and executor loop
depends_on: T05, T08
covers: S5
files: src/specvania/agent/actions.py, src/specvania/agent/executor.py, src/specvania/agent/prompts/warrior.md, tests/test_actions.py, tests/test_executor.py

Each turn the Warrior returns one JSON object `{thought, action, args}` (D3). `thought` is capped at 500 characters and shown in the map's dialogue box. Invalid output gets up to 2 repair prompts with the validation error. Context per attempt: spec summary, the room block, conventions and domain docs, repo map, the room's `files`, and the Paladin's feedback from the previous attempt. The loop ends on `finish`, on `max_steps`, or on a router error.

Checklist:
- [x] Every action shape validates with Pydantic; unknown actions are rejected with the list of valid ones
- [x] JSON wrapped in markdown fences or prose is still extracted when exactly one object is present
- [x] Two invalid outputs in a row are repaired; a third ends the attempt as failed with a clear reason
- [x] The loop stops at `max_steps` and records why
- [x] A scripted run (fake provider) reads a file, edits it, runs an approved command and finishes
- [x] Each action emits an `action` event with a short args summary (no file contents)

### T10 · Paladin: room review, gates and boss evaluation
depends_on: T05, T06
covers: S7, S12
files: src/specvania/agent/reviewer.py, src/specvania/agent/gates.py, src/specvania/agent/prompts/paladin_room.md, src/specvania/agent/prompts/paladin_boss.md, tests/test_reviewer.py, tests/test_gates.py

Gates are the `gates` commands from config, run without a prompt (D8), each emitting `gate_result`. Room review input: room block, attempt diff, gate results. Output JSON `{verdict, checklist[{item, ok, note}], feedback}`; the verdict is forced to `fail` if any checklist item is not ok or any gate failed. Boss evaluation input: the area's boss scenarios, the area diff and gate results; output one `{scenario, passed, reason}` per scenario.

Checklist:
- [x] A failing gate forces `fail` even if the model answers `pass`
- [x] A checklist item marked not ok forces `fail`
- [x] The review verdict, per-item results and feedback are emitted as one `review` event
- [x] Boss evaluation emits one `boss_scenario` per scenario and one `boss_result`
- [x] The Paladin never receives file contents outside the diff and the room's `files`

### T11 · Engine: doors, commits, lives and bosses
depends_on: T04, T06, T09, T10
covers: S4, S7, S12, S13
files: src/specvania/run/engine.py, src/specvania/run/gitops.py, src/specvania/cli.py, tests/test_engine.py, tests/test_gitops.py

`specvania run <dir>` lints the world, checks git (D7), snapshots the spec into the ledger as a spec version, opens a run, creates branch `specvania/<run-id>`, emits `world_loaded`, then clears rooms in topological order (ties by room id). Per room: attempt → gates → review; pass → commit `specvania(Txx): <title>` and `room_cleared`; fail → `respawn` with feedback until lives run out → `run_paused` with retry / skip / abort. When every room of an area is cleared, run the boss; a failed scenario pauses the run. Every event goes to the bus and the ledger.

Checklist:
- [x] A room never starts before all rooms in its `depends_on` are cleared
- [x] Each cleared room creates exactly one commit whose message starts with `specvania(Txx)`
- [x] Dirty tree without `--allow-dirty` exits non-zero before any model call
- [x] No push is ever executed (asserted by inspecting git commands in tests)
- [x] Out of lives → paused; retry resets lives, skip emits `room_skipped` and keeps doors closed for its dependents, abort ends the run with status `aborted`
- [x] A failed boss scenario pauses the run and names the scenario and reason
- [x] The run row references the spec version it executed; editing the spec and running again creates a new version
- [x] A killed run's state is rebuilt from the ledger's events (end-to-end test with the fake provider)

### T12 · Summary, `status`, `history` and `export`
depends_on: T04, T11
covers: S11, S13
files: src/specvania/run/summary.py, src/specvania/pricing.py, src/specvania/cli.py, tests/test_summary.py, tests/test_history.py

Fold a run's events into `summary.md`: per model (calls, tokens in/out, 429s, fallbacks, total latency) and per room (attempts, result, commit). `pricing.toml` entries are `model`, `input_per_mtok`, `output_per_mtok`, `currency`, `source_url`, `checked_on`; cost-equivalent appears only for priced models, with source and date. `status` prints a run with Rich. `history` lists features (status, versions, runs, open bugs) and, for one feature, its spec versions and runs. `export <run-id>` writes `events.jsonl`.

Checklist:
- [x] Summary is generated for finished, aborted and paused runs
- [x] With no `pricing.toml`, no currency value appears anywhere in the output
- [x] Priced models show cost with source URL and check date; unpriced models show "not priced"
- [x] Token and call totals equal the sums over `model_call` events (property test)
- [x] `history <feature>` shows two runs with different spec versions after the scenario in S13
- [x] `export` followed by decoding returns exactly the events stored in the ledger

### T13 · Bugs and the Bestiary
depends_on: T07, T11
covers: S14
files: src/specvania/bugs.py, src/specvania/cli.py, src/specvania/run/engine.py, src/specvania/sdd/planner.py, tests/test_bugs.py

Implement D17. `bug add` (title, optional feature and room), `bug list` (open by default, `--all`), `bug close <id> --wontfix`. The engine opens a bug on `room_skipped` and on a failed boss scenario, with the Paladin's last feedback as body. `plan --from-bug <id>` gives the bug to the Mage as the idea plus context and links the new feature to the bug; when a run of that feature finishes with every room cleared, the bug is closed as fixed by that run. Each change emits `bug_opened` or `bug_closed`.

Checklist:
- [x] Skipping a room opens one bug linked to feature, run and room
- [x] A failed boss scenario opens one bug naming the scenario
- [x] `plan --from-bug` includes the bug title and body in the Mage's input (asserted on the fake provider)
- [x] A fully cleared run of a feature planned from a bug closes that bug with `fixed_by_run`
- [x] `bug close --wontfix` closes without a run; closed bugs only show with `--all`

---

## A4 · Map
boss: S9, S10, S14

### T14 · UI server, tokens and browser launcher
depends_on: T03, T04
covers: S9, S10
files: src/specvania/ui/server.py, src/specvania/ui/launcher.py, src/specvania/ui/static/index.html, src/specvania/ui/static/main.js, src/specvania/cli.py, tests/test_ui_server.py

FastAPI app serving `ui/static/` and one WebSocket `/ws`, bound to `127.0.0.1` (D16). On start it creates a view token and a control token and registers both with the redaction guard. Every page and WebSocket request needs the view token; approval answers need the control token; the WebSocket rejects foreign `Origin` headers. Protocol: server → client `{kind: "state"}` then `{kind: "event"}` messages; client → server `{kind: "approval_answer", request_id, answer}`. Live mode follows the bus; replay mode reads events from the ledger or a `.jsonl` file and plays them with original gaps divided by the speed (gaps capped at 2 s). `launcher.py` opens a browser tab with the view token for `--ui=browser`.

Checklist:
- [x] Requests without the view token get 401; approval messages without the control token are rejected and logged
- [x] A WebSocket handshake with a foreign `Origin` is refused
- [x] A client connecting mid-run receives the current state, then live events, with no gaps or duplicates in `seq`
- [x] Replay at speed 4 is about 4× faster than speed 1 (fake clock test)
- [x] `index.html` and every static file load with no external URLs (asserted by scanning static files)

### T15 · Pixel kit: sprites, palette and vendored assets
depends_on: T01
covers: S9
files: src/specvania/ui/static/pixel/palette.js, src/specvania/ui/static/pixel/sprites.js, src/specvania/ui/static/pixel/textures.js, src/specvania/ui/static/vendor/phaser.min.js, src/specvania/ui/static/vendor/fonts/, src/specvania/ui/static/sprites.html, THIRD_PARTY_NOTICES.md

Original 16×16 sprites defined as palette + pixel strings (D10): Mage, Warrior, Paladin (idle, walk, act, rest frames), boss, monster (idle, defeated), door (locked/open), room tiles per area color, heart, coin, stamina pip. `textures.js` turns them into Phaser textures at startup. Vendor a pinned Phaser 3 build and a pixel font under OFL; record both in `THIRD_PARTY_NOTICES.md`.

Checklist:
- [x] No image or sprite is copied from an existing game or asset pack
- [x] Every sprite has the frames listed above and renders at 1×, 2× and 4× without blur (`pixelArt: true`)
- [x] Phaser version and font license are recorded in `THIRD_PARTY_NOTICES.md`
- [x] `sprites.html` shows every sprite for visual review

### T16 · World map
depends_on: T06, T14, T15
covers: S9
files: src/specvania/ui/static/map/layout.js, src/specvania/ui/static/map/scene.js, src/specvania/run/events.py, tests/test_world_payload.py

Render the world from `world_loaded`: areas left to right, rooms placed by door depth within the area, doors as corridors. Room states: locked (dark, fog), open, active (party inside), cleared (lit), failed, skipped. Camera follows the party; the whole map is visible with a zoom-out key.

Checklist:
- [x] `world_loaded` carries areas, rooms, doors and boss ids (test on the payload)
- [x] This repository's own world (23 rooms, 6 areas) renders without overlapping rooms
- [x] Each room state has a distinct look, also distinguishable in grayscale
- [x] A room changes state within 1 second of its event (manual check, logged in the report)

### T17 · Party HUD, dialogue, monsters and animations
depends_on: T13, T16
covers: S9, S12, S14
files: src/specvania/ui/static/hud/party.js, src/specvania/ui/static/hud/dialogue.js, src/specvania/ui/static/hud/bestiary.js, src/specvania/ui/static/map/animations.js

Party panel: per character the current model id, status (idle / thinking / acting / cooldown / waiting for player), stamina bar from the limiter budget, cooldown countdown, coins when priced or tokens otherwise, lives as hearts. Dialogue box at the bottom shows the latest `thought`, action, review feedback or approval request in RPG text style. Monsters stand in the rooms of open bugs; the Bestiary panel lists open bugs of the feature. Animations: walk between rooms, respawn flash, boss HP bar with one segment per scenario, monster defeated on `bug_closed`.

Checklist:
- [x] Each status maps from specific events (mapping table in a code comment)
- [x] A `fallback` event updates the character's model id on screen
- [x] Boss HP loses exactly one segment per passed scenario
- [x] `bug_opened` places a monster in its room and the Bestiary; `bug_closed` plays the defeat animation and removes it
- [x] Without the control token, approval requests show "waiting for player — answer in the terminal or desktop window"
- [x] `prefers-reduced-motion` disables walk, flash and defeat animations

### T18 · `replay` and synthetic demo
depends_on: T14, T16
covers: S10
files: src/specvania/cli.py, src/specvania/examples/demo/events.jsonl, src/specvania/examples/demo/README.md

`specvania replay <run-id|file|demo> [--speed]` opens the map in replay mode. Ship a synthetic demo run (hand-built `events.jsonl`, clearly labeled as synthetic in its README) that exercises every room state, a cooldown, a fallback, a respawn, a boss fight and a monster. T23 adds a real recorded run.

Checklist:
- [x] `specvania replay demo` works with no config, no API key, no ledger and the network disabled
- [x] The demo includes at least one cooldown, one fallback, one respawn, one boss fight and one bug
- [x] The demo's README states it is synthetic
- [x] `--speed` accepts 1, 2 and 4 and rejects other values

---

## A5 · Desktop
boss: S16, S17

### T19 · Electron shell and `--ui=auto|desktop`
depends_on: T14
covers: S16
files: apps/desktop/package.json, apps/desktop/main.js, apps/desktop/preload.js, apps/desktop/README.md, src/specvania/ui/launcher.py, src/specvania/ui/static/main.js, tests/test_launcher.py

Electron app that opens one frameless 16:9 window (minimum 960×540) and loads the URL passed with `--url`; the control token arrives as a separate `--control-token` argument, never in the URL. The preload exposes a minimal `window.specvaniaDesktop` (`isDesktop`, `controlToken()`, `notify(title, body)`); the web app draws a pixel title bar when it is present. Navigation is locked to the server origin; external links open in the system browser. `launcher.py` finds the app (config `desktop.path`, env `SPECVANIA_DESKTOP`, `specvania-desktop` on PATH, `/Applications/specvania.app` on macOS) for `--ui=desktop`, and `--ui=auto` prefers it and falls back to the browser with a one-line notice.

Checklist:
- [x] `npm --prefix apps/desktop start -- --url <url>` opens the map from a running `specvania replay demo`
- [x] `contextIsolation: true`, `nodeIntegration: false` and `sandbox: true` are set and asserted in a test of the window options
- [x] Navigating to another origin is blocked; `window.open` to http(s) opens the system browser
- [x] `--ui=auto` without the app opens the browser and prints the notice; `--ui=desktop` without the app exits non-zero with install instructions
- [x] The control token never appears in a URL, the ledger or logs

### T20 · Approvals and notifications in the desktop window
depends_on: T08, T19
covers: S16, S17
files: apps/desktop/main.js, apps/desktop/preload.js, src/specvania/ui/static/hud/approval.js, src/specvania/ui/server.py, src/specvania/agent/approval.py, tests/test_desktop_approval.py

With the control token present, `approval_requested` opens an RPG choice box (Allow / Deny) showing the command and cwd. The answer travels over the WebSocket to a desktop responder registered in the `ApprovalBroker`; the first answer wins (D8) and the terminal prompt closes with "approved in desktop window" or "denied in desktop window". Native notifications fire for `approval_requested`, a failed `review` and `boss_result`, only when the window is not focused.

Checklist:
- [x] Approving in the window runs the command and closes the terminal prompt with the right message
- [x] Answering in the terminal first closes the choice box in the window
- [x] A browser tab (view token only) shows the request but cannot answer it
- [ ] Notifications fire for the three events and only when the window is unfocused (manual check, screenshot in the report)

### T21 · Desktop packaging and releases
depends_on: T19
covers: S16
files: apps/desktop/package.json, apps/desktop/electron-builder.yml, apps/desktop/scripts/make-icon.mjs, .github/workflows/desktop-release.yml, THIRD_PARTY_NOTICES.md

Package with `electron-builder`: macOS dmg for arm64 and x64 (unsigned, documented), Linux AppImage. `make-icon.mjs` renders the app icon from the sprite definitions in `pixel/sprites.js` using only Node built-ins. A workflow builds on tags `desktop-v*` and attaches the artifacts to a GitHub Release.

Checklist:
- [ ] The workflow produces a dmg for each macOS architecture and an AppImage
- [x] The icon is generated from the pixel kit, not a hand-drawn image file
- [x] The installed app is found by `launcher.py` on macOS and Linux
- [x] The unsigned-build warning and how to open the app are documented in `apps/desktop/README.md`
- [x] Electron's license and its bundled third-party notices are referenced in `THIRD_PARTY_NOTICES.md`

---

## A6 · Ship
boss: S15

### T22 · Packaging, docs, notices and Postgres end-to-end
depends_on: T12, T13, T18, T20, T21
covers: S10, S15
files: pyproject.toml, README.md, CONTRIBUTING.md, THIRD_PARTY_NOTICES.md, .github/workflows/ci.yml, tests/test_e2e.py

Make the wheel include `specvania/ui/static/` and `specvania/examples/`. Add CI jobs that (a) build the wheel and run `uvx --from dist/*.whl specvania replay demo --help`, and (b) run one end-to-end fake-provider `plan` + `run` + `history` against Postgres from `docker-compose.yml`. README: quickstart (Python only), optional desktop app, optional Postgres team mode, the SDD ↔ game table, model roles, safety notes (D7, D8, D12, D16), how to add prices, and a GIF slot.

Checklist:
- [x] The wheel contains every file under `specvania/ui/static/` and `specvania/examples/`
- [ ] The wheel job and the Postgres end-to-end job pass on CI
- [ ] README quickstart works from a fresh clone with no Docker and no Node (verified by the owner)
- [ ] All spec §11 checklist items are green

---

## A7 · Studio
boss: S18, S19

### T24 · `specvania studio` and the desktop handshake
depends_on: T19, T20
covers: S18
files: src/specvania/ui/studio.py, src/specvania/ui/server.py, src/specvania/cli.py, apps/desktop/main.js, apps/desktop/preload.js, tests/test_studio.py, tests/desktop/studio.test.cjs

`specvania studio` starts the UI server in studio mode with no run (D18): it binds `127.0.0.1:0`, creates the view and control tokens, prints exactly one JSON line `{"port", "view_token", "control_token"}` on stdout and logs only to stderr. Started without `--url`, the desktop app spawns it through the login shell (D19: `$SHELL -lic 'exec <specvania> studio'`, with the path from the app settings or `specvania` on `PATH`), reads the handshake and loads the game; the control token reaches the renderer through the preload, never through argv or a URL. Studio phases: `idle` → `project_open` → `planning` → `world_ready` → `running` → `finished` (back to `project_open` on demand). A `shutdown` control message finishes an active run as aborted and exits 0; quitting the app sends it and kills the child after 5 s. Until T33 exists, a failed start shows a plain error page.

Checklist:
- [x] `specvania studio` prints exactly one handshake line on stdout and serves the game under the existing token rules (D16)
- [x] Started without `--url`, the desktop app spawns the studio through the login shell and opens the game; the spawn command line and environment contain no token (Node test with a fake child process)
- [x] If the studio exits or no handshake arrives within 20 s, the app shows an error page with a non-secret reason and the log path
- [x] `shutdown` finishes an active run with `run_finished` status aborted and the process exits 0; quitting the app sends it
- [x] Control messages without the control token are rejected like approval answers and leave the connection open

### T25 · Plan and run from the studio
depends_on: T24
covers: S18
files: src/specvania/ui/studio.py, src/specvania/ui/server.py, src/specvania/sdd/planner.py, src/specvania/run/engine.py, src/specvania/run/gitops.py, tests/test_studio_flow.py

Control messages (control token): `open_project{path}` (must be inside a git work tree; reports whether `.specvania/` exists), `init_project`, `start_plan{idea, no_clarify}`, `clarify_answer{question_id, answer}`, `save_world{slug}` (D25: commits only `.specvania/specs/<slug>/` with the message `specvania: map <slug>`), `start_run{slug, allow_dirty}` (a dirty tree is refused with the list of dirty paths unless `allow_dirty`), `pause_decision{choice}`, plus the existing `approval_answer`. Plan and run execute in a worker thread; the planner's `answer`, the engine's `decide` and an `ApprovalBroker` responder wait on the WebSocket. Pending Clarify questions travel like pending approvals: a transient prompt with the options and the default, restored on reconnect. After each change the studio sends `studio_state{phase, project, worlds, open_bugs}`. Messages that do not fit the phase are rejected without changing state.

Checklist:
- [x] A fake-provider test drives `open_project` → `start_plan` → Clarify answers → `save_world` → `start_run` → approval → `pause_decision` → finished over the WebSocket only, with one commit per cleared room on `specvania/<run-id>` and no terminal prompt
- [x] `save_world` commits only the world folder; other dirty files stay uncommitted
- [x] `start_run` on a dirty tree is refused with the dirty paths unless `allow_dirty` is true
- [x] A message sent in the wrong phase (for example `start_run` while planning) is rejected and the phase does not change
- [x] A pending Clarify question, with its options and default, is restored after a reconnect

### T26 · Room files and diff endpoint
depends_on: T24
covers: S18
files: src/specvania/ui/server.py, src/specvania/ui/studio.py, src/specvania/run/gitops.py, tests/test_room_code.py

`GET /api/rooms/<id>/code` (view token) returns, for a cleared room, the files changed by its commit and the commit diff; for the active room, the working-tree diff against the attempt base; for any other room, the files listed in its `files:` line with their current content. Every path passes the Sandbox rules (D12: no `.git/`, ignored or secret paths, nothing outside the root) and every body passes the redaction guard. Caps: 200 kB per file and 1 MB per response, truncated with a visible marker. When the repository is not available (a replay of an exported run), the endpoint answers 404 with a reason.

Checklist:
- [ ] A cleared room returns its commit's files and diff; the active room returns the working diff
- [ ] `.git/`, `.env`, ignored files and paths outside the root are never returned (one test per case)
- [ ] Files and responses above the caps are truncated with the marker
- [ ] A token value written into a repository file is redacted in the response
- [ ] Requests without the view token get 401

### T27 · Stamina as a token budget and the party probe
depends_on: T25
covers: S19
files: src/specvania/config.py, src/specvania/run/engine.py, src/specvania/run/events.py, src/specvania/run/state.py, src/specvania/provider/client.py, src/specvania/cli.py, src/specvania/ui/studio.py, tests/test_budget.py, tests/test_probe.py

D21: `limits.token_budget` (positive integer, default 1,000,000) is sent as optional `run_started.token_budget`. Before each model call of a run, if the tokens spent in the run (input + output) reach the budget, the run pauses with reason "stamina exhausted"; `retry` emits the new event `stamina_restored{token_budget}` with the budget plus 25% of the original and continues, `skip` is rejected for this pause, `abort` finishes the run. `RunState` projects the budget, tokens spent and tokens spent per role; cooldowns no longer change stamina (DEV-009's estimate is removed). D24: `specvania doctor --probe` and the studio's `probe_party` send one minimal completion to each distinct configured model through the provider (no router, no fallback) and report ok with latency, the HTTP status or a timeout; the CLI exits non-zero if any model fails. Tests use the fake provider only.

Checklist:
- [ ] A run whose spending reaches the budget pauses before the next model call with "stamina exhausted"
- [ ] Retry emits `stamina_restored` with the original budget × 1.25 and the run continues; skip is rejected for this pause; abort finishes as aborted
- [ ] `run_started.token_budget` and `stamina_restored` round-trip through `events.jsonl`, and older streams without them still load
- [ ] `RunState` exposes budget, spent and per-role spent; a `cooldown` no longer changes stamina
- [ ] `doctor --probe` lists every distinct model as ok with latency or with its HTTP status or timeout, and exits non-zero when any fails
- [ ] `probe_party` returns the same results to the game

### T28 · Agent language
depends_on: T25
covers: S21
files: src/specvania/config.py, src/specvania/sdd/planner.py, src/specvania/sdd/prompts/, src/specvania/agent/executor.py, src/specvania/agent/reviewer.py, src/specvania/agent/prompts/, src/specvania/ui/studio.py, tests/test_language.py

D22: `[ui] language` is `"en"` or `"pt-BR"` (default `"en"`); the studio's `set_language{language}` overrides it for the session. Mage, Warrior and Paladin prompts get one instruction to write human-facing text — Clarify questions and options, spec prose, room titles and checklist items, the Warrior's `thought`, the Paladin's feedback — in the chosen language, and to keep code, identifiers, file paths, commit message prefixes and world-format keywords in English. A change applies from the next model call.

Checklist:
- [ ] The chosen language reaches every Mage, Warrior and Paladin prompt (fake provider captures the messages)
- [ ] A world written in Brazilian Portuguese by the fake Mage passes `specvania lint`
- [ ] `set_language` during a run applies from the next model call
- [ ] Unknown language values are rejected by the config and by the studio

---

## A8 · Castle
boss: S20

### T29 · Kenney Tiny Dungeon and the new cast
depends_on: T15
covers: S20
files: src/specvania/ui/static/vendor/kenney-tiny-dungeon/, src/specvania/ui/static/pixel/cast.js, src/specvania/ui/static/pixel/textures.js, src/specvania/ui/static/pixel/sprites.js, src/specvania/ui/static/sprites.html, apps/desktop/scripts/make-icon.mjs, THIRD_PARTY_NOTICES.md, tests/desktop/cast.test.cjs

D10: vendor the pack's packed 16×16 tilesheet and its `License.txt` (CC0). `cast.js` maps names to tile indices: Mage 84, Warrior 87, Paladin 96; monsters from 108, 120, 121, 122 and 123, chosen by a stable hash of the bug id; bosses from 109, 110 and 111 by area; walls, back-wall bricks, floors, doors, stairs, banners, chests and grates. `textures.js` slices the sheet into Phaser textures. The code-drawn sprites in `sprites.js` are removed once nothing references them. `sprites.html` shows the whole cast. The app icon is generated from the Mage tile with Node built-ins only.

Checklist:
- [x] The pack and its license are vendored and listed in `THIRD_PARTY_NOTICES.md`
- [x] Every cast entry resolves to a tile inside the sheet (Node test)
- [x] The same bug id always gets the same monster
- [x] `sprites.html` shows every cast entry at 2× and 3× without blur
- [x] The app icon is generated from the pack with Node built-ins only

### T30 · Castle layout and room states
depends_on: T16, T29
covers: S20
files: src/specvania/ui/static/map/castle.js, src/specvania/ui/static/map/layout.js, src/specvania/ui/static/map/scene.js, tests/desktop/castle.test.cjs

D23: `castle.js` is a pure function from the `world_loaded` payload to floors (one per area, in order), chambers (rooms left to right by door depth, then id), doors between consecutive chambers (locked until every dependency of the next room is cleared), a boss arena at the end of each floor and stairs down to the next floor. The scene draws the cross-section with the cast: walls, back-wall bricks, floors, banners and labels. Room states: locked (dark, padlocked door), open (lit torch), active (warm light, party inside), cleared (open chest, green check), failed (red tint), skipped (grey, cobwebs). The camera follows the party; `M` toggles the whole castle; clicking a chamber dispatches `specvania:room-selected`. Integer scaling only; the canvas fills the window.

Checklist:
- [x] The layout is deterministic and has no overlapping chambers for this repository's world and for the sample project's world
- [x] Every floor ends in its boss arena, and stairs connect it to the next floor
- [x] Each room state has a distinct look, also distinguishable in grayscale (screenshot in the report)
- [x] Clicking a chamber dispatches its room id; `M` toggles the whole-castle view
- [x] The canvas fills a 960×540 window and larger ones with integer scaling only

### T31 · Castle animations
depends_on: T17, T30
covers: S20
files: src/specvania/ui/static/map/animations.js, src/specvania/ui/static/map/scene.js, tests/desktop/animations.test.cjs

The cast has one frame per character, so motion comes from tweens: hops while walking between chambers and down stairs; the Warrior's weapon swing on `action`; Mage sparkles while planning; the Paladin's shield on `review`; shake and a lost heart on a failed review; return to the chamber entrance on `respawn`; zzz while in `cooldown`; boss hit per passed scenario and defeat or escape on `boss_result`; chambers revealed one by one as a new world arrives; monster defeat on `bug_closed`. The event → animation mapping is a table in a code comment. `prefers-reduced-motion` and the reduced-motion setting replace tweens with instant state changes.

Checklist:
- [ ] Each listed event triggers its animation (mapping test with a fake scene)
- [ ] Reduced motion disables every tween and keeps every state change visible
- [ ] Replaying the demo at 4× keeps the animation queue bounded (test on the queue)

---

## A9 · Screens
boss: S21, S22

### T32 · Languages in the UI and the HUD overlay
depends_on: T27, T30
covers: S21
files: src/specvania/ui/static/i18n/index.js, src/specvania/ui/static/i18n/en.json, src/specvania/ui/static/i18n/pt-BR.json, src/specvania/ui/static/hud/, tests/desktop/i18n.test.cjs

D22: `t(key, params)` serves every visible string: statuses, room states, dialogue templates, buttons, errors and HUD labels; PT-BR terms follow `docs/domain.md`. The HUD is drawn over the castle: hearts, the stamina bar (tokens spent / budget, or tokens only when the run has no budget), party portraits with model id, status, tokens spent and coins when priced, and the `PT | EN` switch. The language comes from the app settings in the desktop window and from `navigator.language` in a browser tab.

Checklist:
- [ ] `en.json` and `pt-BR.json` have the same keys and the same placeholders (test)
- [ ] No user-visible string literal remains outside the dictionaries (scan test with an allowlist for model ids and numbers)
- [ ] Switching the language updates every visible text without a reload
- [ ] The stamina bar shows spent and budget from `RunState`, and tokens only when there is no budget

### T33 · Title, project, settings and error screens
depends_on: T24, T32
covers: S22
files: src/specvania/ui/static/screens/title.js, src/specvania/ui/static/screens/project.js, src/specvania/ui/static/screens/settings.js, src/specvania/ui/static/screens/error.js, apps/desktop/main.js, apps/desktop/preload.js, tests/desktop/screens.test.cjs

Title: logo, New project, Open project, Watch demo, language, Settings and the Kenney credit. Project: native folder picker (preload `pickFolder()`), git check, init offer, the project's worlds and open bugs, Check the party (`probe_party`, green or red per model with latency). Settings: language, token budget, path to `specvania`, reduced motion; stored in the app's user-data folder and sent to the studio. Error: studio failed to start, missing API key (with where to set `NVIDIA_API_KEY`), connection lost. Every screen works with keyboard and mouse.

Checklist:
- [ ] Every screen is reachable and usable with the keyboard only
- [ ] A folder outside a git work tree shows the reason; a git folder without `.specvania/` offers init
- [ ] Settings persist across app restarts (Node test with a temporary user-data folder)
- [ ] The missing-key error explains where to set the key and still offers Watch demo
- [ ] The preload exposes only `isDesktop`, `controlToken`, `notify`, `pickFolder`, `settings` and `studio` status (asserted)

### T34 · Idea, Clarify and saving the map
depends_on: T25, T28, T33
covers: S22
files: src/specvania/ui/static/screens/idea.js, src/specvania/ui/static/hud/dialogue.js, src/specvania/ui/static/map/scene.js, tests/desktop/clarify.test.cjs

At the castle entrance the Mage asks for the idea in a dialogue box with a text field. Each Clarify question appears as RPG dialogue with selectable options, a free-text field and the default highlighted. The chambers of the new world appear one by one; then Save map (`save_world`) and Start expedition (`start_run`), which lists dirty paths with Cancel / Start anyway when the tree is dirty.

Checklist:
- [ ] The idea and each answer travel as studio messages (Node test with a fake socket)
- [ ] Enter accepts the default; options are selectable with arrow keys and the mouse
- [ ] Start expedition stays disabled until the map is saved
- [ ] A dirty tree lists the dirty paths and offers Cancel / Start anyway

### T35 · Room panel with Monaco
depends_on: T26, T32
covers: S22
files: src/specvania/ui/static/vendor/monaco/, src/specvania/ui/static/hud/room-panel.js, src/specvania/ui/server.py, THIRD_PARTY_NOTICES.md, tests/test_ui_server.py, tests/desktop/room-panel.test.cjs

D20: vendor a pinned Monaco min build trimmed to the editor core and the Python, JavaScript, TypeScript, JSON, Markdown, TOML, YAML, HTML, CSS and shell languages, within 8 MB. Read-only, no language services. Clicking a chamber slides a panel over 60% of the window with tabs Mission (checklist and item states), Code (file list and editor), Diff (side-by-side diff editor) and Review (Paladin verdict and gate results). Esc closes it. It also works in a browser tab.

Checklist:
- [ ] Monaco loads with the network disabled in the browser tab and in the desktop window
- [ ] The vendored size is within 8 MB; version and license are in `THIRD_PARTY_NOTICES.md`
- [ ] The four tabs show data for a cleared, an active and a locked room
- [ ] The editor is read-only: typing changes nothing
- [ ] The CSP has no remote sources and no `'unsafe-eval'` (test on the response headers)

### T36 · Expedition screens
depends_on: T31, T32, T34
covers: S22
files: src/specvania/ui/static/hud/approval.js, src/specvania/ui/static/screens/pause.js, src/specvania/ui/static/screens/boss.js, src/specvania/ui/static/screens/end.js, tests/desktop/expedition.test.cjs

The T20 approval choice box restyled and translated, with keyboard support. The pause dialog ("The party fell!") with Retry / Skip / Abort and one line explaining each; for "stamina exhausted", Rest / Abort. The boss arena transition with one HP segment per scenario. The end screen with the run summary — rooms, tokens, calls, fallbacks, time and coins when priced — plus See commits and Back to the castle.

Checklist:
- [ ] Approvals, pause choices and stamina choices work with keyboard and mouse and reach the studio
- [ ] The pause dialog shows the `run_paused` reason, translated when it is a known reason
- [ ] The end screen numbers match `summary.md` for the same run (fixture test)
- [ ] The boss loses one HP segment per passed scenario in the arena

### T37 · Watch the demo in the app
depends_on: T33, T36
covers: S23
files: src/specvania/ui/studio.py, src/specvania/ui/static/screens/title.js, src/specvania/ui/static/screens/replay.js, src/specvania/examples/demo/, tests/test_studio_replay.py

Watch demo asks the studio (`open_replay`) to play a bundled recording — `real-run` when present, otherwise the synthetic `demo` — through the same game screens. A recording may hold a plan stream and a run stream, played in sequence, so the Clarify answers and approvals appear as they happened. Speed 1×, 2× or 4×, changeable during playback, and pause. No API key, no project and no network.

Checklist:
- [ ] Watch demo works with no API key, no project and the network disabled
- [ ] Speed 1, 2 and 4 change the pace (fake clock test) and can change during playback
- [ ] A recording with a plan stream and a run stream plays both in order, showing the recorded Clarify answers and approvals

---

## A10 · Demo
boss: S23

### T38 · Package and document v0.3
depends_on: T35, T37
covers: S23
files: apps/desktop/package.json, apps/desktop/electron-builder.yml, README.md, README.pt-BR.md, THIRD_PARTY_NOTICES.md, pyproject.toml, .github/workflows/ci.yml, tests/test_readme.py

The dmg opens the title screen; a first-run screen explains that the game needs `specvania` installed (`uv tool install`) and the API key in the shell. README in English and Brazilian Portuguese with the same sections: what it is, a GIF slot, install (uv tool and dmg), play, the SDD ↔ castle table, safety notes (D7, D8, D12, D16, D19), credits (Kenney, Press Start 2P, Phaser, Monaco, Electron). The wheel includes the i18n files and the vendored Kenney and Monaco assets.

Checklist:
- [ ] The wheel contains the i18n files and the vendored Kenney and Monaco assets
- [ ] `README.md` and `README.pt-BR.md` have the same section headings (test)
- [ ] The installed dmg opens the title screen and Watch demo works without an API key (owner check)
- [ ] `THIRD_PARTY_NOTICES.md` lists Kenney Tiny Dungeon, Monaco, Phaser, Press Start 2P and Electron with their licenses

### T23 · Dogfood run in the game and real demo
depends_on: T38
covers: S11, S23
files: examples/sample-todo-api/, src/specvania/examples/real-run/, .agent/deviations.md, README.md, README.pt-BR.md

The sample project already exists in `examples/sample-todo-api/` (FastAPI todo API with tests). With a real NVIDIA API key, the owner plays one feature end to end in the desktop game: Check the party, idea, Clarify, Save map, expedition, approvals, boss, end screen. Export the plan and run streams as `real-run`, bundled as the in-app Watch demo; make the README GIF from it; use the summary to settle O1 (default models per role).

Checklist:
- [ ] One full plan → run cycle completes in the desktop game on the sample project (or the failure is documented)
- [ ] `real-run` (plan and run streams) and `summary.md` are committed and replay correctly in the game
- [ ] O1 is decided and logged in `.agent/deviations.md` with the numbers from the summary
- [ ] The README GIF comes from the real run, not the synthetic one
