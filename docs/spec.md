# specvania — Spec v0.4

> A spec-driven coding agent where **your spec becomes a metroidvania castle**.
> A party of open models (served by NVIDIA NIM by default) plans, codes and reviews your work. You direct it from a desktop game — type the idea, answer the Mage, approve commands — and watch the party clear the castle built from your spec, room by room, in English or Brazilian Portuguese. A dialogue box always tells you what is happening and when it is your turn, with music and sound.

| | |
|---|---|
| Status | Approved by the owner (2026-10-13), including the v0.3.1 amendment; v0.4 amendment approved on 2026-10-15 |
| Owner | @rotirotirafa |
| Date | 2026-10-15 |
| Read with | [`docs/domain.md`](domain.md) · [`docs/conventions.md`](conventions.md) · [`docs/tasks.md`](tasks.md) · [`.agent/execution-protocol.md`](../.agent/execution-protocol.md) |

**Changelog**
- v0.4 (2026-10-14) — Playability, after the v0.3 launch. The owner's dogfood run showed the game is not intuitive: "I don't know what to do at the current step" (C22). A JRPG-style dialogue box at the bottom of the castle says who is acting and what they did, and becomes "your turn" when the player must answer; approvals, Clarify questions and pauses move into it, with keys (C23, D28). The party thinks out loud with playful verbs next to the real wait, without spending tokens (C24, D29). A step chip shows where the player is in the six-step journey, and `?` explains the current step (C25, D30, D31). Music from CC0 files and sound effects synthesized in code (C26, D32). New area A12 (S25–S27, T42–T47), after the launch (C27). Providers beyond an OpenAI-compatible endpoint move to v0.5.
- v0.3.1 (2026-10-13) — Launch: the CLI ships on PyPI and the desktop app on GitHub Releases, from a tag the owner pushes (C19, D26); a new last area A11 prepares the public repository (community files, sponsor), the release automation and the launch kit with the recorded demo and LinkedIn drafts (C20, D27, S24, T39–T41). The dogfood run (T23) happens in a fresh repository the owner creates. Providers beyond an OpenAI-compatible endpoint (Claude, Codex, OpenClaw, Hermes…) are v0.4 (C21).
- v0.3 (2026-10-13) — The desktop game becomes the main surface: a long-lived `specvania studio` server started by the Electron app runs plan and run without a terminal (C12–C18). The map becomes a castle cross-section drawn with the Kenney Tiny Dungeon pack (CC0), replacing the code-drawn sprites (D10). Read-only code and diff per room in an embedded Monaco editor. Stamina becomes a per-run token budget. The game, the agents' prose and the README in English and Brazilian Portuguese. Party probe before an expedition. T23 moves to the new last area so the dogfood run happens in the game. Purpose: a demo and portfolio piece.
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
- G7. Play the whole cycle — pick a project, idea, Clarify, save the world, run, approvals, pauses, bosses, summary — inside the desktop game, without a terminal.
- G8. Offer the game and the agents' human-facing text in English and Brazilian Portuguese.
- G9. Be demo-ready: someone who watches about two minutes of the game understands the concept without an explanation, and a recorded real run plays offline inside the app.
- G10. Be launch-ready: installable from PyPI and GitHub Releases, in a repository that welcomes issues, contributions and sponsors, with the launch material ready for the owner to publish.
- G11. Be playable: at every moment the game says what is happening and whether it is the player's turn, every player action has a key, and the castle has music, sound effects and a party that thinks out loud.

## 3. Non-goals (v0.4)

- Editing code inside the app, an integrated terminal, VS Code or other IDE extensions; Docker sandbox for command execution; parallel task execution.
- Several projects, runs or windows at once in the game; switching projects during a run.
- Bundling Python inside the desktop app (the game needs `specvania` installed for the user; see O5).
- Redis or any message broker (D14).
- A hosted server, user accounts or authentication beyond local tokens (D16).
- Pushing branches or opening PRs (specvania commits locally, never pushes).
- Native function-calling (D3), multiplayer, approvals from a plain browser tab.
- Long-context summarization of agent history (truncation only).
- Signed and notarized desktop builds; Windows as a first-class platform (best effort).
- Providers other than an OpenAI-compatible endpoint — native Claude, Codex, OpenClaw, Hermes or other agents as party members (v0.5, C21, C27).
- Distribution through npm (C19).
- Publishing, tagging, posting or making the repository public from automation: the launch rooms prepare everything, the owner presses the buttons (S-7).
- Pausing an expedition from the game at any moment: a run still pauses only on its own (stamina, lives, a failed boss, an exhausted chain).
- Spoken voices, a sound per typed letter, music chosen by the player, gamepad input, and thought balloons on the map (C24).

## 4. Clarify log

| # | Question | Answer |
|---|---|---|
| C1 | Product shape | Terminal CLI (Devin / Claude Code style) — superseded by C12 in v0.3; the CLI stays as the engine and for scripting |
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
| C12 | Product shape (v0.3) | A desktop game where the agents code and the player directs: idea, Clarify, approvals, pauses and code review all happen in the game window, never in a terminal |
| C13 | Purpose and success | Demo and portfolio piece; success is a viewer understanding the concept from about two minutes of use, without an explanation. Visual polish weighs more than robustness |
| C14 | Map interaction | A live map the player clicks (no player-controlled character); clicking a room opens its code and diff |
| C15 | Art direction | Castle cross-section: each room is a stone chamber seen from the side, drawn with the Kenney Tiny Dungeon pack (CC0) |
| C16 | Stamina | The tokens available and spent: a per-run token budget |
| C17 | Languages | The product in English and Brazilian Portuguese, switchable in the game |
| C18 | Front-end approach | Evolve the existing web app and Electron shell (no bundler); a long-lived studio server bridges the engine to the game |
| C19 | Distribution (v0.3.1) | The CLI on PyPI (`uvx specvania`, `uv tool install specvania`) and the desktop app on GitHub Releases; no npm package |
| C20 | Launch (v0.3.1) | A last area after the dogfood run prepares the public repository (contributing, issue forms, code of conduct, security policy, GitHub Sponsors), the release automation and the launch kit (recorded demo, screenshots, LinkedIn drafts in English and Portuguese); the owner publishes and posts |
| C21 | Providers beyond NIM | Wanted next: use any provider or agent — Claude, Codex, OpenClaw, Hermes and others — instead of being tied to NVIDIA NIM; v0.5 since C27, brainstormed and specified after v0.4 |
| C22 | Playability (v0.4) | After the dogfood run the game was "not intuitive — I don't know what to do at the current step". Every moment must say what is happening and whether it is the player's turn, and the game should feel like one: music, sounds on buttons, on finished work and on monsters, a help menu, and characters thinking out loud |
| C23 | Layout (v0.4) | A JRPG-style dialogue box at the bottom, the castle in the rest of the window, the step and the party as small chips at the top; chosen from mockups over a mission bar and a journey sidebar |
| C24 | Thoughts (v0.4) | Like Claude Code's waiting words (churning, confabulating, scheming): a playful verb that changes by itself next to the real wait, in the dialogue box's last line; chosen over balloons on the map. No model writes them |
| C25 | Help (v0.4) | `?` explains the current step — what happens, when it is your turn, the keys that work now, what comes next — with every key one Tab away; chosen over a plain controls list and an Esc game menu |
| C26 | Sound (v0.4) | Music from CC0 files, sound effects synthesized in code; on by default, with music and effects volumes and mute in Settings |
| C27 | Order (v0.4) | Playability ships after the v0.3 launch, as v0.4; providers move to v0.5 |

## 5. How SDD maps to the game

| SDD concept | specvania | On the map |
|---|---|---|
| Spec (`spec.md` + `tasks.md`) | World | The whole castle |
| Task group / milestone | Area | A floor of the castle |
| Task | Room | A stone chamber, dark and locked until its doors open |
| `depends_on` | Door | A door between chambers; unlocks when every room it depends on is cleared |
| Task checklist passes review | Room cleared | Chamber lights up, its chest opens, a commit is made |
| Scenarios assigned to an area | Boss | Boss arena at the end of the floor; one HP segment per scenario |
| Planner / Coder / Reviewer | Mage / Warrior / Paladin | The party |
| Token budget of a run | Stamina | Party bar in the HUD; tokens spent per character |
| 429 backoff | Cooldown | Character rests (zzz), timer shown |
| Reviewer rejects | Respawn | Warrior returns to the room entrance with feedback |
| Max attempts per room | Lives | Hearts in the HUD |
| Cost-equivalent | Coins | Counter per character |
| Open bug | Monster | A monster in the room where the bug was found; listed in the Bestiary |
| Project history | Ledger | The save file |

Full glossary and naming rules: [`docs/domain.md`](domain.md).

## 6. User-facing surface (v0.4)

**The game (main surface).** Open the specvania app from the Dock. It starts `specvania studio` and shows the title screen:

```
Title ── New project / Open project ── pick a git folder (init offered) ── Check the party
  │                                         │
  │                                         └─ Idea → Clarify (dialogue boxes) → castle revealed → Save map
  │                                               → Start expedition → approvals, pauses, bosses → end screen
  ├─ Watch demo (bundled real run, 1×/2×/4×, offline)
  ├─ Language: EN | PT-BR
  └─ Settings: language, token budget, path to specvania, reduced motion, music and effects volume, mute
Click any chamber → panel with Mission (checklist), Code, Diff (read-only Monaco) and Review.
```

**In the castle (v0.4).** The castle fills the window; a dialogue box at the bottom says who is acting and what they did, and turns gold when it is the player's turn (D28):

```
[5/6 Expedition · T03] [Mage  Warrior!  Paladin  ♥♥♡]                       [mute] [?]

              castle cross-section — the camera follows the party; M shows it whole

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ [portrait] Warrior                                                                    │
│ I wrote src/total.py. Now I'll run the room's tests.                                  │
│ ✻ Hammering… (12 s · z-ai/glm-5.3)                                                    │
└──────────────────────────────────────────────────────────────────────────────────────┘
  your turn:  "May I run npm test in T03?"   A approve · N deny (reason) · V view command
```

Keys (D31): `?` or F1 help for the current step (Tab: every key) · Esc close or back · M whole castle · arrows move the camera · A / N / V in an approval · 1–n or ↑↓ Enter to pick a Clarify option or a pause choice · in Watch demo 1 / 2 / 4 and Space.

**The CLI (engine, scripting, CI).** Everything the game does goes through the same engine; the CLI keeps working:

```
specvania studio               # started by the desktop app; serves the game and runs plan/run from it
specvania init                 # create .specvania/config.toml in the current git repo
specvania doctor [--probe]     # check API key, models, ledger connection; --probe calls each model once
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
  config.toml                  # provider (incl. timeout_s), roles → model chains, limits (incl. token_budget), gates, ledger, ui (incl. language)
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

Studio mode (v0.3), started from the Dock:

```
 Electron app ──spawn via login shell ($SHELL -lic)──► specvania studio (Python, long-lived)
      ▲                                                   │  one JSON handshake line on stdout:
      └────────── port + view token + control token ◄─────┘  {"port", "view_token", "control_token"}
      │
      └─ loads the game ──► WebSocket: events ▲  control messages ▼ (control token)
                              open_project · start_plan · clarify_answer · save_world · start_run
                              approval_answer · pause_decision · probe_party · set_language
                              open_replay · shutdown
         studio ──► planner `answer`, engine `decide`, ApprovalBroker responder (same engine as the CLI)
         GET /api/rooms/<id>/code ──► files + diff per room (read-only, Sandbox + redaction)
```

Playability (v0.4) lives in the web app and is derived from what the studio already sends — no new event, no new control message, so live play, Watch demo and old ledgers behave the same:

```
 studio_state + events ─┬─► play/step.js ────► step chip (1–6, current room)
                        ├─► play/narrator.js ─► dialogue box: speaker, line, your turn, keys ◄── play/thinking.js (verbs, timer)
                        ├─► play/keys.js ─────► existing control messages: approval_answer · clarify_answer · pause_decision
                        └─► audio/cues.js ────► audio/engine.js ──► music (vendored CC0 OGG) + effects (audio/sfx.js, Web Audio)
```

## 9. Decisions

| Id | Decision | Why |
|---|---|---|
| D1 | Python ≥ 3.12, `uv`, Typer, Rich, Pydantic v2, `openai` SDK, FastAPI + uvicorn, SQLAlchemy 2 (Core) + Alembic; `psycopg` 3 only in the `postgres` extra | Owner's core stack; one data layer for both databases |
| D2 | Any OpenAI-compatible endpoint; default `base_url` is `https://integrate.api.nvidia.com/v1`; key read from an env var (default `NVIDIA_API_KEY`) | NIM is the default, not a lock-in; Ollama, OpenRouter etc. work by changing config |
| D3 | Model-agnostic **JSON action protocol**: each Warrior turn returns exactly one JSON action; parsed and validated with Pydantic; up to 2 repair retries on invalid output | Catalog models differ in native tool-calling support; one protocol works for every model and is easy to log and render. Native tool-calling is future work |
| D4 | Three roles with ordered fallback chains in `config.toml`. Initial defaults (snapshot of `/v1/models` on 2026-10-12, **quality not yet verified**): Mage `moonshotai/kimi-k3` → `z-ai/glm-5.3` → `nvidia/nemotron-3-ultra-550b-a55b`; Warrior `z-ai/glm-5.3` → `moonshotai/kimi-k2.6` → `deepseek-ai/deepseek-v4.1-flash`; Paladin `nvidia/nemotron-3-ultra-550b-a55b` → `deepseek-ai/deepseek-v4.1-flash` → `z-ai/glm-5.3-flash` | Strong reasoning to plan, strong agentic coder to execute, a different family to review (avoids a model grading itself). T05 verifies ids; T23 validates quality. A probe on 2026-10-13 found `moonshotai/kimi-k2.6` listed but not served (HTTP 404) and `deepseek-ai/deepseek-v4.1-flash` unresponsive (DEV-010–DEV-012); O1 settles the chains |
| D5 | One token-bucket limiter per API key, shared by all roles (default 40 RPM). 429 / 5xx → exponential backoff with jitter, honoring `Retry-After`. After 3 consecutive failures on a model, the role moves to the next model in its chain for the rest of the room. An HTTP 404 moves to the next model at once (DEV-012). Requests time out after `provider.timeout_s` (default 300 s, DEV-011) | The free tier limit is per key, not per model; catalogs list models that are not served |
| D6 | **Event sourcing**: every state change is an immutable event appended to the ledger's `events` table, the run's source of truth. Run state, the terminal view, the map and `summary.md` are projections. `export` writes `events.jsonl`; `replay` accepts a run id or a `.jsonl` file | One source of truth; live and replay share the same code path; runs stay portable and shareable |
| D7 | `run` requires a git repo and a clean tree (unless `--allow-dirty`), creates branch `specvania/<run-id>` from `HEAD`, makes one commit per cleared room, never pushes | Every room is a rollback point; nothing leaves the machine |
| D8 | Model-proposed shell commands always need approval, in the terminal **or** the desktop window; the first answer wins and the other side shows how it was resolved. Gate commands written by the user in `config.toml` run without a prompt | Owner decisions C6 and C11; gates are the user's own instructions, not model output |
| D9 | UI is one web app (vanilla ES modules + vendored Phaser 3, pinned, MIT) served by FastAPI with one WebSocket. Two shells load it: a browser tab and the Electron app in `apps/desktop/` (plain JS main and preload, no bundler, `electron-builder` for packaging). `--ui=auto` prefers the desktop app when installed and falls back to the browser. Since v0.3 the desktop app also starts its own studio (D18, D19); the game screens that need control (idea, Clarify, run, approvals) appear only with the control token | No duplicated UI code; the Python package works without Node; the desktop adds a game window, native notifications and control |
| D10 | Art comes from the **Kenney Tiny Dungeon** pack (CC0, 16×16), vendored with its license; credit shown on the title screen and in the notices. Pixel font vendored under OFL. No characters, tiles or names from existing games or other packs. (v0.2 used sprites defined in code; replaced in v0.3, C15) | A demo needs art a viewer reads as a castle at a glance; CC0 keeps it open-source safe with no attribution burden |
| D11 | No prices are shipped. `pricing.toml` is user-maintained, each entry with source URL and date. Summary always shows tokens; coins only appear for models with a configured price | Invented numbers would undermine trust in the published results |
| D12 | Files sent to models skip `.git/`, ignored paths and secret patterns (`.env*`, `*.pem`, `*.key`, `id_*`); file tools cannot read or write outside the repo root | Basic safety for a local agent |
| D13 | Limits: max 25 Warrior steps per attempt, 3 lives per room, observations truncated to 8 kB each | Bounded cost per room; all configurable |
| D14 | **Ledger behind one storage port.** SQLite at `.specvania/ledger.db` by default; Postgres when `SPECVANIA_DATABASE_URL` is set. Same SQLAlchemy Core schema and Alembic migrations for both, applied automatically on start. Tables: `projects`, `features`, `spec_versions`, `runs`, `events`, `bugs`. **No Redis**: within one process the event bus is in memory; cross-process notification, if ever needed, uses Postgres `LISTEN/NOTIFY` | Zero setup for the first run; team mode without code changes; one service fewer to install and explain |
| D15 | `docker-compose.yml` provides Postgres only, for team mode and for the Postgres test job. Docker is never required to use specvania | Keeps `uvx specvania` working on a clean machine |
| D16 | UI server binds to `127.0.0.1` and issues two random tokens per process: a **view token** (required to load the page and open the WebSocket) and a **control token** (required to answer approvals), passed only to the desktop app at launch. The WebSocket checks the `Origin` header. A browser tab gets the view token only | Any web page can try to reach localhost; approving shell commands must require a secret only the desktop app holds |
| D17 | **Bugs** come from `bug add`, from a room that runs out of lives and is skipped, and from a failed boss scenario. Lifecycle: `open` → `fixed` (closed automatically when a run planned with `--from-bug` clears) or `wontfix`. Open bugs render as monsters in their room and in the Bestiary | Failures become tracked work instead of lost log lines |
| D18 | **Studio.** `specvania studio` is a long-lived UI server with one session: one project and at most one active plan or run. The game drives it with control messages on the existing WebSocket, each requiring the control token (D16). The engine is unchanged: the studio plugs the planner's `answer`, the engine's `decide` and an `ApprovalBroker` responder into the WebSocket, and runs plan and run in a worker thread | One engine for CLI and game; the game is another responder, not a second implementation |
| D19 | **Launch from the Dock.** The Electron app starts the studio through the user's login shell (`$SHELL -lic 'exec <specvania> studio'`), so `PATH` and the API key arrive as in the terminal; the key never passes through Electron. The studio prints one JSON handshake line (port, view token, control token) on stdout; tokens never go to argv, files or logs. The path to `specvania` can be set in Settings. Quitting the app sends `shutdown`: an active run finishes as aborted | macOS apps opened from the Dock do not inherit the shell environment; argv is visible to other users through `ps` |
| D20 | **Code view.** A read-only Monaco editor (pinned min build, MIT, trimmed to the needed languages, vendored) shows each room's files and diff from `GET /api/rooms/<id>/code` (view token). Files pass the Sandbox rules (D12) and the redaction guard, with size caps. No editing, no language services | "An IDE as a game" needs real code reading; read-only keeps the agents as the only writers |
| D21 | **Stamina is a token budget.** `limits.token_budget` (default 1,000,000 input + output tokens per run) travels as optional `run_started.token_budget`. Before each model call of a run the engine compares tokens spent with the budget; when it is reached the run pauses with "stamina exhausted", and the player rests (retry: +25% of the original budget, `stamina_restored` event) or aborts. Plan runs show tokens but have no cap. Cooldowns render as rest and no longer change stamina (replaces the DEV-009 estimate) | Tokens are what a run actually spends; the free tier exposes no balance, so the budget is the player's |
| D22 | **Two languages.** Every UI string comes from `i18n/en.json` or `i18n/pt-BR.json` (same keys); PT-BR terms follow `docs/domain.md`. Prompts ask for human-facing text — Clarify questions and options, spec prose, room titles and checklists, the Warrior's `thought`, the Paladin's feedback — in the chosen language; code, identifiers, file paths and world-format keywords stay in English, so lint is unchanged. The game defaults to the OS language; the CLI uses `[ui] language` | C17; one engine, two audiences |
| D23 | **Castle layout.** A pure function turns a world into floors (one per area, in order), chambers (rooms left to right by door depth, then id), doors between consecutive chambers (locked until every dependency of the next room is cleared), a boss arena at the end of each floor and stairs to the next floor. The camera follows the party; `M` shows the whole castle | Deterministic and testable; the cross-section reads as a castle (C15) |
| D24 | **Party probe.** `doctor --probe` and the game's "Check the party" send one minimal completion to each distinct configured model through the provider (no router, no fallback) and report ok with latency, the HTTP status, or a timeout. Owner-run only (real key) | Catalog presence did not prove a model answers (T05/T23 findings) |
| D25 | **Saving the world.** After Clarify the game commits only `.specvania/specs/<slug>/` on the current branch ("specvania: map <slug>") before the run. If other files are dirty, the game lists them and asks to cancel or start anyway (`--allow-dirty`) | The v0.2 flow required a manual commit between plan and run (T23 friction) |
| D26 | **Distribution.** One version, in `pyproject.toml`, drives everything. Pushing a tag `vX.Y.Z` runs a release workflow that builds the sdist and wheel and publishes them to PyPI through trusted publishing (OIDC, no token stored in the repository), builds the desktop app (unsigned dmg arm64 and x64, AppImage) and creates the GitHub Release with the artifacts, `SHA256SUMS` and the version's `CHANGELOG.md` section. Pull requests run the same builds without publishing. The owner sets up the PyPI trusted publisher and pushes the tag | The CLI is a Python package (PyPI is where `uvx` finds it); Releases carry the desktop builds; OIDC avoids long-lived secrets in a public repository |
| D27 | **Community.** `CONTRIBUTING.md` explains the SDD contract (spec, rooms, gates, deviations) and the local setup; issue forms for a bug, a feature idea and a room proposal; a pull-request template with the gate checklist; `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1); `SECURITY.md` pointing to GitHub private vulnerability reporting; `.github/FUNDING.yml` for GitHub Sponsors (@rotirotirafa). Everything in English, with the README in both languages | A portfolio project that invites contributions needs a front door; issue forms keep reports actionable |
| D28 | **Dialogue box and chips.** In the castle, the map fills the window and a dialogue box sits at the bottom: the speaker's portrait and name, their latest real line (the Warrior's `thought` and action, the Paladin's feedback, a gate result, a bug opened or closed, a boss scenario, an approval result) and the thinking line (D29). When the player must answer — an approval, a Clarify question, a run pause — the box becomes **your turn**: gold border, the alert sound, the question and its choices with keys. The approval modal and the pause screen are replaced by the box, and the Clarify screen uses the same box; the full command opens with V; a deny reason (T20) is typed in the box under the same rules. At the top left, a step chip (D30) and a party chip (portraits, stamina hearts, `!` on whoever waits for the player) that opens today's party panel and Bestiary as a drawer; at the top right, mute (D32) and `?` (D31). The room panel (D20) is unchanged. Everything is derived in the web app from `studio_state` and the event stream; a replay never shows a turn | C22, C23; one place to look, the castle stays in view, and live play and replays share one projection (D6) |
| D29 | **Thinking line.** While a party member is thinking — the party HUD's existing state: a role from its `model_requested` until its `model_call` or action, the Paladin while gates and reviews run — the box's last line reads `✻ <verb>… (<elapsed> · <model>)`, with `gates` in place of the model while gates run, and the box speaks for that role. Verbs come from per-role lists in `i18n/en.json` and `i18n/pt-BR.json`, at least 12 per role and language, drawn at random without repeating the previous verb, changing every 4 s; the elapsed time counts from the event that started the wait. With reduced motion the verb stays and the ✻ does not pulse. Verbs never come from a model and spend no tokens | C24; a wait feels alive, while the Warrior's own `thought` stays the real line |
| D30 | **Journey steps.** Six steps derived in the web app from the studio phase and the screen on display: 1 Project (title, project screen, party check), 2 Idea, 3 Clarify (the Mage's questions and the spec being written), 4 Map (castle revealed, saving the map), 5 Expedition (the run, with its approvals, pauses and bosses), 6 End. The step chip shows `n/6 <step>` and, in the expedition, the current room. Watch demo shows the step of the moment it replays | C22; the player always knows where they are in the whole cycle |
| D31 | **Step help and keys.** `?` or F1 opens the card of the current step: what happens, when it is your turn, the keys that work now and what comes next; Tab in the card lists every key by step; Esc closes it. The first time a viewer reaches a step, the box adds "First time here? Press ? to learn this step" (remembered in browser storage; without storage the hint simply shows again). Keys: A approve, N deny (optional reason), V view the full command; 1–n or ↑↓ Enter pick a Clarify option or a pause choice; M whole castle; arrows move the camera; Esc closes or goes back; Watch demo keeps 1 / 2 / 4 and Space. Keys are the same in both languages. No key fires while a text field has focus, and keys that answer the studio do nothing without the control token (D16) | C25; every player action has a key, explained where it is used |
| D32 | **Sound.** Music: vendored OGG loops, CC0 only, each listed with its source URL and license in `THIRD_PARTY_NOTICES.md` and in a manifest a test checks, all under 4 MB: a calm theme (steps 1–4), an exploration loop (expedition), a boss loop and two end jingles (every room cleared, or not); tracks crossfade when the step changes. Effects: synthesized in code with Web Audio in an 8-bit style, no files: hover, confirm, back, help; your turn; room entered, gate passed, bug opened, bug closed, room cleared, room failed or skipped, boss appears, boss defeated, stamina exhausted, stamina restored; one short blip per new line in the box. Settings gain music volume (default 40 %), effects volume (default 70 %) and mute, saved with the other settings; the mute chip toggles it. Watch demo plays sound. The same effect never repeats within 120 ms. Without audio output the game stays silent and never fails. The desktop app allows autoplay; in a browser, sound starts at the first click or key. The CSP allows media only from the app itself | C26; files make the music sound like a game, synthesized effects need no assets or licenses |

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

### S18 · Drive a run through the studio
```gherkin
Given specvania studio is running and a client holds its control token
When the client opens a git project, sends an idea, answers the Clarify questions, saves the map and starts the run
And answers every approval and pause from the same connection
Then each cleared room is committed on specvania/<run-id> exactly as with the CLI
And nothing is written to or read from a terminal
And a client with only the view token can watch, but every control message it sends is rejected
```

### S19 · Stamina is a token budget
```gherkin
Given limits.token_budget is 200000
When the party has spent 200000 tokens in the run
Then the run pauses before the next model call with "stamina exhausted"
When I choose to rest
Then the budget grows to 250000 and the run continues
And the HUD shows the tokens spent and the budget, for the party and per character
```

### S20 · The castle reflects the spec
```gherkin
Given a world with three areas
Then the map shows three floors, one chamber per room in door order, each floor ending in its boss arena
And stairs lead from each boss arena to the next floor
And every chamber state follows its room's events within 1 second
And each state is distinguishable in grayscale
```

### S21 · Two languages
```gherkin
Given the language is Brazilian Portuguese
Then every screen, button, status and dialogue template of the game is in Brazilian Portuguese
And the Mage asks the Clarify questions and writes the spec prose in Brazilian Portuguese
And code, file paths and world-format keywords stay in English, so specvania lint still passes
When I switch to English
Then the game changes at once and the agents follow from their next model call
```

### S22 · From idea to commit without a terminal
```gherkin
Given the desktop app and specvania are installed for the user
When I open the app from the Dock, pick a project folder and type an idea
Then I answer the Clarify in dialogue boxes, save the map and start the expedition
And I approve commands, open any chamber to read its checklist, code, diff and review
And I reach the end screen with the run summary
And I never type in a terminal
```

### S23 · Offline demo of a real run
```gherkin
Given the desktop app is installed, no API key is set and the network is disabled
When I choose Watch demo on the title screen
Then a run recorded by specvania on the sample project plays from the idea to the end screen
And its Clarify answers and approvals appear as they happened
And I can switch between 1×, 2× and 4× while it plays
```

### S24 · From visitor to user and contributor
```gherkin
Given the public GitHub repository and its latest release
When a visitor follows the README
Then `uvx specvania --version` installs the CLI from PyPI and prints the release version
And the desktop app from the release opens the title screen and plays Watch demo offline
And the visitor can open a bug, a feature idea or a room proposal through an issue form
And the README and CONTRIBUTING.md explain how to contribute and how to sponsor the project
```

### S25 · I always know what to do
```gherkin
Given I play in the desktop game in Brazilian Portuguese
Then a chip shows the current step, from "1/6 Projeto" to "6/6 Fim", and the current room during the expedition
And the dialogue box at the bottom shows who is acting and their latest line
When the Warrior asks to run a command
Then the box turns gold with the command and the keys A, N and V, and the your-turn sound plays
And pressing A approves it, and pressing N lets me type a reason and deny it
When I press ?
Then a card explains the current step: what happens, when it is my turn, the keys that work now and what comes next
And every key of every step is one Tab away
And no shortcut fires while I type in a text field
```

### S26 · The party thinks out loud
```gherkin
Given the Warrior is waiting for its model
Then the last line of the dialogue box reads "✻ <verb>… (<seconds> · <model>)"
And the verb comes from the Warrior's list in the current language and changes every 4 seconds without repeating
And the seconds count from the model request
And no model call is made and no token is spent for these lines
Given reduced motion is on
Then the verb stays the same until the wait ends
```

### S27 · The castle has sound
```gherkin
Given sound is on
Then the title, the expedition and the boss fight each have their own music
And the end screen plays a victory jingle when every room is cleared, and another jingle otherwise
And my turn, a room cleared, a monster appearing and defeated, and a boss appearing and falling each have their own sound effect
When I set music to 0 and effects to 50 % in Settings, or press the mute chip
Then the game follows at once and keeps the choice on the next start
Given Watch demo plays at 4×
Then no sound effect repeats within 120 ms
Given the computer has no audio output
Then the game plays silently without errors
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
- [ ] The studio never writes a token to argv, files or logs; the desktop app's spawn command line contains no token (covered by a test).
- [ ] The game loads with the network disabled, including the vendored Kenney tiles and Monaco, with a CSP that has no remote sources and no `'unsafe-eval'`.
- [ ] `i18n/en.json` and `i18n/pt-BR.json` have the same keys and placeholders (covered by a test).
- [ ] Kenney Tiny Dungeon (CC0) and Monaco (MIT) are listed with their licenses in `THIRD_PARTY_NOTICES.md`.
- [ ] Every music file is CC0 and listed with its source URL and license in `THIRD_PARTY_NOTICES.md` and the music manifest; all music stays under 4 MB and ships in the wheel (covered by a test).
- [ ] The game's CSP allows media only from the app itself and still has no remote sources and no `'unsafe-eval'` (covered by a test).
- [ ] Each role has at least 12 thinking verbs in each language (covered by the i18n test).
- [ ] No keyboard shortcut fires while a text field has focus, and no key answers the studio without the control token (covered by a test).

## 12. Definition of done

**v0.3.** All scenarios S1–S24 pass, the technical checklist is green, the desktop app builds for macOS (arm64, x64) and Linux (AppImage) in CI, and the full cycle of S22 works from the Dock. The package ships a real recorded run (`real-run`, plan and run) produced by specvania through the game on a fresh repository the owner creates (the sample project is the fallback), playable as Watch demo next to the synthetic `demo`. The README exists in English and Brazilian Portuguese, and its GIF comes from the real run. The release workflow, the community files and the launch kit are ready; publishing to PyPI, creating the release and posting are the owner's steps.

**v0.4.** Scenarios S25–S27 pass and the technical checklist stays green. The owner plays one expedition with sound on, from the idea to the end screen, without asking what to do next, and confirms the A12 boss (G8). Watch demo shows the dialogue box, the step chip, the thinking line and the sound.

## 13. Open points (decide during execution, log in `.agent/deviations.md`)

- O1. Final default models per role after T23 measures quality on the sample project.
- O2. Whether the Paladin should also review the spec itself before a run (beyond lint).
- O3. Map layout algorithm details if worlds grow beyond ~30 rooms.
- O4. How the desktop app is distributed beyond GitHub Releases (Homebrew cask, etc.).
- O5. Bundling a Python runtime with the desktop app so it works without `uv tool install specvania`.
- O6. Moving the `run --ui` desktop launch to the D19 handshake too, so the control token leaves argv on that path as well.
- O7. Which CC0 tracks to vendor (D32): Claude picks candidates and checks each license before T47; the owner hears them in G8.
- O8. Re-recording the README GIF and the launch video with the v0.4 interface and sound.
