# specvania

[![CI](https://github.com/rotirotirafa/specvania/actions/workflows/ci.yml/badge.svg)](https://github.com/rotirotirafa/specvania/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/specvania.svg)](https://pypi.org/project/specvania/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[English](README.md) · [Português brasileiro](README.pt-BR.md)

## What it is

**Your spec becomes a metroidvania castle. The agents code; you direct the party.**

The Mage turns your idea and Clarify answers into a world, the Warrior works
through its rooms, and the Paladin reviews each checklist and fights the area's
boss scenarios. Areas become floors, dependencies become doors, and cleared
rooms become local commits. The ledger remembers features, spec versions, runs
and bugs. Open any chamber to read its mission, code, diff and review.

The desktop game, studio, castle, read-only Monaco panel and offline Watch demo
are implemented in English and Brazilian Portuguese. **Not released yet —
PyPI and downloadable builds come with the first release.** The bundled demo
is synthetic; a recording of a real run will replace it.

## Install

Install the Python CLI first (Python 3.12+, Git and [uv](https://docs.astral.sh/uv/)),
then the desktop app. Python is not bundled in the app. The commands below work
from a clone today; commands using the PyPI name and downloadable release
artifacts apply **once released**.

From the repository clone:

```sh
uv tool install --compile-bytecode .
```

Once released on PyPI:

```sh
uv tool install --compile-bytecode specvania
uvx specvania --version
```

`uvx specvania` runs the CLI without a permanent install; the app needs the
persistent `uv tool install` installation on the login shell's PATH (or its
executable path in Settings). For actual expeditions, export your provider key
in the profile read by that shell, for example `~/.zshrc`:

```sh
export NVIDIA_API_KEY='<your key>'
```

Then choose **Restart studio** in the error screen, or reopen the app.
The first-run screen offers installation help when the CLI cannot run. Watch
demo remains visible there and becomes enabled after the CLI starts; it needs
**no API key**. A missing-key error still lets you watch the demo.

Get the desktop app from [GitHub Releases](https://github.com/rotirotirafa/specvania/releases)
when v0.3.0 is published. See **Optional desktop app** below for unsigned-build
and macOS opening instructions.

## Play

1. Open the app from the Dock to reach the **title** screen; choose EN or PT.
2. Choose **New project** or **Open project** and select a Git folder with a
   commit at HEAD and a configured Git author. Initialize specvania if offered.
   **Check the party** probes the configured models and needs a provider key.
3. Start a new expedition and type your **idea**. Answer the Mage's **Clarify**
   questions; Enter accepts the default. The castle reveals your world.
4. Choose **Save map** to commit the world, then **Start expedition**. If other
   files are dirty, choose Cancel or explicitly start anyway.
5. Direct the expedition: approve commands, choose Retry / Skip / Abort when
   the party falls, or Rest / Abort when stamina runs out. Click a chamber for
   the **room panel**: Mission, Code, Diff and Review. Monaco is read-only.
6. Watch the Paladin fight each floor's boss. The **end screen** shows rooms,
   tokens, calls, fallbacks, time and coins when priced, with See commits and
   Back to the castle.

**Watch demo** on the title screen plays the bundled recording offline, without
an API key, project or provider connection, at 1×, 2× or 4× with pause. It shows
recorded Clarify answers and approvals. It requires the installed CLI, just as
the rest of the game does. The current recording is synthetic.
The UI and its vendored art, font, Phaser and Monaco load locally; live
planning and coding contact your configured provider.

## Python quickstart

You need Python 3.12 or newer, Git, and [uv](https://docs.astral.sh/uv/).
The Python package uses SQLite by default and needs neither Docker nor Node.
From a fresh clone:

```sh
cd specvania
uv sync --locked
uv run specvania --help
uv run specvania replay demo
```

`replay demo` opens the map with a **synthetic** bundled run; it needs no config,
API key or provider connection. Close the terminal process with Ctrl+C after
watching. See [the demo's provenance](src/specvania/examples/demo/README.md).

To make the CLI available in your own repository, install from the clone:

```sh
uv tool install --compile-bytecode .
```

Once the package is published on PyPI, invoke it without a permanent install:

```sh
uvx specvania replay demo
```

For actual planning and execution, enter an existing Git repository with a
commit at HEAD and a configured Git author. Set your provider key in the
environment, then:

```sh
specvania init
export NVIDIA_API_KEY='<your key>'
specvania doctor
git add .gitignore .specvania/config.toml
git commit -m "configure specvania"
specvania plan "add greeting"
specvania lint .specvania/specs/add-greeting
git add .specvania/specs/add-greeting
git commit -m "specify greeting"
specvania run .specvania/specs/add-greeting --ui=browser
specvania history add-greeting
specvania status
specvania export <run-id>
specvania replay <run-id>
```

The Mage normally asks Clarify questions; Enter accepts a question's default.
Use `--no-clarify` to skip that step. A hand-written `spec.md` + `tasks.md` world
also works after `specvania lint <spec-dir>` passes. Commit the world before
running: `run` requires a clean tree unless you explicitly use `--allow-dirty`.
`export` writes `.specvania/runs/<run-id>/events.jsonl`; replay also accepts that
file. Summaries are saved beside it as `summary.md`.

## SDD ↔ game

| SDD concept | specvania | On the map |
|---|---|---|
| Spec (`spec.md` + `tasks.md`) | World | The whole castle |
| Task group / milestone | Area | A floor of the castle |
| Task | Room | A stone chamber, dark and locked until its doors open |
| `depends_on` | Door | Unlocks when every dependency is cleared |
| Task checklist passes review | Room cleared | Chamber lights up, chest opens, a commit is made |
| Scenarios assigned to an area | Boss | Arena at the end of the floor; one HP segment per scenario |
| Planner / Coder / Reviewer | Mage / Warrior / Paladin | The party |
| Token budget of a run | Stamina | Party bar in the HUD; tokens spent per character |
| 429 backoff | Cooldown | Character rests; timer shown |
| Reviewer rejects | Respawn | Back to the room entrance with feedback |
| Max attempts per room | Lives | Hearts in the HUD |
| Cost-equivalent | Coins | Only for models with configured prices |
| Open bug | Monster | In its room and the Bestiary |
| Project history | Ledger | The save file |

## Model roles and chains

The default provider is the OpenAI-compatible NVIDIA NIM endpoint,
`https://integrate.api.nvidia.com/v1`. Edit `.specvania/config.toml` to change
`provider.base_url`, the API-key environment variable name, or any role's ordered
model chain. Each role uses the first healthy model and falls back after repeated
429/5xx failures, or immediately on HTTP 404. The party shares one rate limiter per API key (40 RPM by default).
`provider.timeout_s` sets the request timeout in seconds (positive finite float; default 300).

These defaults are a **2026-10-12 catalog snapshot, unverified for quality**.
A model listed in the catalog may still be unavailable or fail to respond.
`doctor` checks configured ids; `doctor --probe` makes a minimal completion per
distinct model. Both need a provider key and contact the configured endpoint.

| Role | Work | Default chain, in order |
|---|---|---|
| Mage | Clarify, spec and rooms | `moonshotai/kimi-k3` → `z-ai/glm-5.3` → `nvidia/nemotron-3-ultra-550b-a55b` |
| Warrior | Execute one room through JSON actions | `z-ai/glm-5.3` → `moonshotai/kimi-k2.6` → `deepseek-ai/deepseek-v4.1-flash` |
| Paladin | Review checklists and evaluate boss scenarios | `nvidia/nemotron-3-ultra-550b-a55b` → `deepseek-ai/deepseek-v4.1-flash` → `z-ai/glm-5.3-flash` |

## Optional desktop app

The Electron shell in [apps/desktop](apps/desktop/README.md) starts the Python
studio through your login shell and opens the title screen. It adds approval
controls and native notifications. Install a matching
macOS dmg (`arm64` or `x64`) or Linux AppImage when release artifacts are
available. Builds are **unsigned**, and macOS builds are **not notarized**;
open the dmg matching your Mac (arm64 for Apple Silicon, x64 for Intel) and drag
`specvania.app` into `/Applications`. For a build you trust, right-click the app
in Finder and choose **Open**, then confirm. If macOS still blocks it, open
**System Settings → Privacy & Security → Open Anyway** after the blocked launch.
On Linux, make the downloaded AppImage executable and open it.
Building the shell from source needs Node/npm; using Python with a browser does
not.

The launcher finds `desktop.path` in config, then `SPECVANIA_DESKTOP`, then
`specvania-desktop` (or a Linux AppImage) on PATH, then
`/Applications/specvania.app` on macOS. `specvania run <spec-dir> --ui=auto`
prefers the installed app and prints a notice when it falls back to a browser.
`--ui=desktop` requires the app; `--ui=browser` opens a view-only tab.
`replay demo` uses the automatic launcher too.

## Optional Postgres team mode

SQLite at `.specvania/ledger.db` is the default ledger. For team mode, install
the `postgres` extra and start the one optional service from the clone:

```sh
uv tool install --compile-bytecode --force '.[postgres]'
docker compose up -d --wait postgres
export SPECVANIA_DATABASE_URL='postgresql+psycopg://specvania:specvania@localhost:5432/specvania'
```

Run the same `init`, `plan`, `run`, `history` and `replay` commands in your project
with that variable set. Migrations apply automatically. The variable selects
the ledger; it does not copy existing SQLite history into Postgres. The compose
credentials are for local development only, and the port binds to loopback.
Unset `SPECVANIA_DATABASE_URL` to return to SQLite. Use `docker compose down`
from the clone when finished; no Redis service is required.

## Safety

- **Git:** `run` creates `specvania/<run-id>` from HEAD, commits once per
  cleared room, and never pushes. Review the local branch before publishing it.
- **Commands:** every model-proposed shell command needs explicit approval
  in the terminal or desktop window. The first answer wins and cancels the
  other prompt. User-written `gates` in config run without an approval prompt.
  Shell commands execute locally with your permissions; this is not a container
  sandbox.
- **Files:** model context skips `.git/`, ignored paths and secret patterns
  (`.env*`, `*.pem`, `*.key`, `id_*`). File tools resolve symlinks and cannot read
  or write outside the repository root. These file-tool restrictions do not
  constrain an approved shell command.
- **Local UI:** the server binds to `127.0.0.1`. A random view token is
  required for page and WebSocket access; the WebSocket checks Origin. A browser
  tab only watches. Desktop control messages require a separate control token.
  Keep view URLs private.
- **Dock launch:** Electron starts `specvania studio` through
  `$SHELL -lic`. Export the provider key in the profile the login shell reads
  (e.g. `~/.zshrc`): the key never passes through Electron. The studio returns
  port and tokens in one stdout handshake; in studio mode the tokens never go
  to argv, files or logs. Settings can override the CLI path.
- **CLI desktop launch:** `run --ui=desktop` still passes the control token
  as a process argument (`--control-token`), visible to local process inspection.
  That path does not use the studio handshake. Tokens are kept out of the ledger,
  exported events and logs; the control token is kept out of URLs.
- API keys live in environment variables; config stores only their names.
  Registered secrets are rejected from event and report output. Execution is
  bounded by configurable steps, lives and observation sizes.

## Add reference prices

No prices are shipped. Summaries always report tokens. To show coins for
a model, create `.specvania/pricing.toml` with one `[[prices]]` table per model.
Replace every angle-bracket placeholder below with the model id, published
nonnegative rates **per million tokens**, currency, source URL and check date
before saving; this is a template, not a price quote:

```toml
[[prices]]
model = "<model id>"
input_per_mtok = <published input rate>
output_per_mtok = <published output rate>
currency = "<currency>"
source_url = "<https://source-of-the-price>"
checked_on = <YYYY-MM-DD>
```

`specvania status <run-id>` uses the file. Coins appear only for priced models,
with `source_url` and `checked_on`; other models remain labeled `not priced`.
Rates describe a user-maintained reference cost-equivalent, not a provider bill.

## Real-run GIF

**GIF placeholder — a recording of a real plan → run cycle will appear here.**
The bundled demo is synthetic; a recording of a real run will replace it.

## Credits

- Kenney Tiny Dungeon — CC0 1.0; castle tiles, party and monsters.
- Press Start 2P — SIL Open Font License (OFL) 1.1.
- Phaser — MIT; castle rendering.
- Monaco — MIT; read-only code and diff.
- Electron — MIT; desktop shell (also ships its runtime's third-party notices).

Versions, provenance and license texts: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Contributing

Explore [the spec](docs/spec.md) and [the rooms](docs/tasks.md).
Read [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md), and the
[execution protocol](.agent/execution-protocol.md). Contributions follow the
same SDD contract that specvania executes.
Use the [issue forms](https://github.com/rotirotirafa/specvania/issues/new/choose)
to report a bug, suggest a feature or propose a room.

[MIT](LICENSE). Castle art comes from Kenney Tiny Dungeon (CC0), as credited above.
Not affiliated with NVIDIA.

## Sponsor

Support maintenance and development through
[GitHub Sponsors — @rotirotirafa](https://github.com/sponsors/rotirotirafa).
Bug reports, documentation and room proposals are also welcome.
