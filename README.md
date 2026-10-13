# specvania

**A spec-driven coding agent where your spec becomes a metroidvania map.**

The Mage plans a world, the Warrior clears its rooms, and the Paladin reviews
each checklist and fights the area's boss scenarios. The ledger keeps features,
spec versions, runs and bugs. Watch the same local web app in a browser tab or
the optional desktop window, live or as a replay.

The Python CLI and synthetic demo are implemented. Release CI, the owner's
fresh-clone quickstart check, and the real dogfood run (T23) remain to be verified.
See [the spec](docs/spec.md) and [the rooms](docs/tasks.md).

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
uv tool install .
```

For a published release, you can instead invoke it without a permanent install:

```sh
uvx --from specvania specvania replay demo
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
| Spec (`spec.md` + `tasks.md`) | World | The whole map |
| Task group / milestone | Area | A region with its own color |
| Task | Room | Locked until its doors open |
| `depends_on` | Door | Opens when the source room is cleared |
| Task checklist passes review | Room cleared | Lights up; one commit is made |
| Scenarios assigned to an area | Boss | One HP segment per scenario |
| Planner / Coder / Reviewer | Mage / Warrior / Paladin | The party |
| Rate-limit budget | Stamina | Bar under each character |
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
429/5xx failures. The party shares one rate limiter per API key (40 RPM by default).

These D4 defaults are a **2026-10-12 catalog snapshot, unverified for quality**.
The real catalog check is an owner check in T05; O1/T23 will settle the chains
after a real run. `doctor` checks the configured ids against your endpoint.

| Role | Work | Default chain, in order |
|---|---|---|
| Mage | Clarify, spec and rooms | `moonshotai/kimi-k3` → `z-ai/glm-5.3` → `nvidia/nemotron-3-ultra-550b-a55b` |
| Warrior | Execute one room through JSON actions | `z-ai/glm-5.3` → `moonshotai/kimi-k2.6` → `deepseek-ai/deepseek-v4.1-flash` |
| Paladin | Review checklists and evaluate boss scenarios | `nvidia/nemotron-3-ultra-550b-a55b` → `deepseek-ai/deepseek-v4.1-flash` → `z-ai/glm-5.3-flash` |

## Optional desktop app

The Electron shell in [apps/desktop](apps/desktop/README.md) loads the same web
app and adds approval controls and native notifications. Install a matching
macOS dmg (`arm64` or `x64`) or Linux AppImage when release artifacts are
available. Builds are **unsigned**, and macOS builds are **not notarized**;
follow the linked installation instructions for opening a build you trust.
Building the shell from source needs Node/npm; using Python with a browser does
not. Installer production still needs release CI verification.

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
uv tool install --force '.[postgres]'
docker compose up -d --wait postgres
export SPECVANIA_DATABASE_URL='postgresql+psycopg://specvania:specvania@localhost:5432/specvania'
```

Run the same `init`, `plan`, `run`, `history` and `replay` commands in your project
with that variable set. Migrations apply automatically. The variable selects
the ledger; it does not copy existing SQLite history into Postgres. The compose
credentials are for local development only, and the port binds to loopback.
Unset `SPECVANIA_DATABASE_URL` to return to SQLite. Use `docker compose down`
from the clone when finished; no Redis service is required.

For the offline provider end-to-end checks against both adapters:

```sh
uv sync --locked --extra postgres
SPECVANIA_TEST_DATABASE_URL="$SPECVANIA_DATABASE_URL" uv run pytest -q -rs tests/test_e2e.py
```

Only the configured test ledger host is allowed through the test socket guard.

## Safety

- **Git (D7):** `run` creates `specvania/<run-id>` from HEAD, commits once per
  cleared room, and never pushes. Review the local branch before publishing it.
- **Commands (D8):** every model-proposed shell command needs explicit approval
  in the terminal or desktop window. The first answer wins and cancels the
  other prompt. User-written `gates` in config run without an approval prompt.
  Shell commands execute locally with your permissions; this is not a container
  sandbox.
- **Files (D12):** model context skips `.git/`, ignored paths and secret patterns
  (`.env*`, `*.pem`, `*.key`, `id_*`). File tools resolve symlinks and cannot read
  or write outside the repository root. These file-tool restrictions do not
  constrain an approved shell command.
- **Local UI (D16):** the server binds to `127.0.0.1`. Page and WebSocket access
  require a random view token, and the WebSocket checks Origin. A browser tab
  only watches; terminal approvals remain available. The desktop gets a separate
  control token to answer approvals. The **desktop control token is passed as a
  process argument** (`--control-token`), which can be visible to local process
  inspection. It is kept out of URLs, the ledger, exported events and logs.
  Keep view URLs private too.
- API keys live in environment variables; config stores only their names.
  Registered secrets are rejected from event and report output. Execution is
  bounded by configurable steps, lives and observation sizes (D13).

## Add reference prices

No prices are shipped (D11). Summaries always report tokens. To show coins for
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

**GIF placeholder — T23 will record the real plan → run cycle on the sample
project and place its GIF here.** The bundled synthetic demo is not that evidence.

## Contributing and licenses

Read [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md), and the
[execution protocol](.agent/execution-protocol.md). Contributions follow the
same SDD contract that specvania executes.

[MIT](LICENSE). Original pixel art; vendored Phaser, font and desktop runtime
licenses are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Not affiliated with NVIDIA.
