# Contributing to specvania

Humans and coding agents follow the same Spec-Driven Development contract.
Start with [AGENTS.md](AGENTS.md) and read, in its listed order, the
[execution protocol](.agent/execution-protocol.md),
[validation gates](.agent/validation-gates.md), [domain vocabulary](docs/domain.md),
[conventions](docs/conventions.md), [spec](docs/spec.md), [rooms](docs/tasks.md)
and [accepted deviations](.agent/deviations.md). Decisions D1–D35 are binding.

## Local setup

Install Python 3.12 or newer, Git and [uv](https://docs.astral.sh/uv/). From the
repository root, sync the locked environment and put its tools on PATH:

```sh
uv sync --locked
export PATH="$PWD/.venv/bin:$PATH"
specvania --help
```

Run the checks below from the same shell. Once `.venv` is synced, these commands
use its tools directly without another sync. No provider key is needed for tests.

Install Node 22 and npm for the desktop checks (the same Node major as CI):

```sh
npm --prefix apps/desktop run check
```

This script uses Node built-ins (`node --check` and `node --test`); it needs no
`node_modules` or package installation. Running or packaging the Electron app
has separate setup in [apps/desktop/README.md](apps/desktop/README.md).
Docker and Postgres are optional; use them only for the G9 check below or team mode.

## Propose a room

Use the [room proposal issue form](https://github.com/rotirotirafa/specvania/issues/new?template=room.yml).
Include the area and room title, `depends_on` doors, `covers` scenario ids,
expected `files`, and checklist items written as binary statements. Each item
needs a test or command that could fail if it were broken, or an explicitly
marked manual/owner check. Use `none` where the world format permits it.
The maintainer reviews the scope, assigns the room id and authorizes the work.

For a problem without a room design, use the
[bug or feature idea forms](https://github.com/rotirotirafa/specvania/issues/new/choose).
Remove API keys and tokens from all reports and attachments. Report vulnerabilities
privately as described in [SECURITY.md](SECURITY.md). Follow our
[Code of Conduct](CODE_OF_CONDUCT.md) in all project spaces.

## How the project is built

The spec defines scenarios and decisions; rooms cover those scenarios, doors
set their order, and gates G1–G10 supply evidence before review and clearing.
The area ends with an owner review of its boss scenarios.

Work on one authorized room at a time, with its doors open. Announce the room,
checklist and expected files. Keep changes within its `files:` line and trivial
wiring; write evidence for each checklist item and tick only items you proved.
Do not change completed rooms or add dependencies outside the conventions'
allowed list without following the protocol's stop rules S-1–S-7.

Log any change to spec intent in `.agent/deviations.md` using its template and
the next free DEV number, newest first. Describe what changed, why, impact and
approval status; use `Approved by: pending` until approved. Do not silently edit
the spec. When blocked, report the question, options and recommendation.

Follow the coding rules in [docs/conventions.md](docs/conventions.md): type hints,
typed exceptions for user-fixable errors, Rich terminal output only in `cli.py`,
no module-level mutable state, time through `Clock`, and every visible UI string
through i18n with matching English and PT-BR keys. Use the domain vocabulary.

## Checks and review

With the venv tools on PATH:

```sh
ruff check .
ruff format --check .
pyright
pytest -q
specvania lint docs/
npm --prefix apps/desktop run check
```

Safety rules: no network in tests except the configured test Postgres host;
no real API keys, credentials or UI tokens in fixtures, logs or reports. Use
`provider/fake.py`, fake keys and `FakeClock`. The socket guard in
`tests/conftest.py` stays active; never disable it to reach a provider or download
an asset. Only the configured `SPECVANIA_TEST_DATABASE_URL` host is allowed.
Git tests use temporary repositories, never the contributor's working repository.
Keep repository boundaries, secret redaction, shell approvals and UI token checks
intact; a browser tab remains a watcher without control authority.

| Gate | Evidence required |
|---|---|
| G1 | `ruff check .` has no errors |
| G2 | `ruff format --check .` needs no reformatting |
| G3 | `pyright` reports zero errors |
| G4 | `pytest -q` passes with the socket guard active |
| G5 | Each ticked checklist item maps to a test, command or screenshot in the report |
| G6 | T15–T21, T23, T24, T29–T38 and T41 have the required visual screenshot or GIF |
| G7 | From T06 on, `specvania lint docs/` prints OK |
| G8 | The owner reviews each area's boss scenarios at the end of that area |
| G9 | Ledger, engine and history rooms, including T22, pass against Postgres |
| G10 | T19–T21 and T24–T41 pass `npm --prefix apps/desktop run check` |

For G9, start the optional local ledger and run the full suite:

```sh
docker compose up -d --wait postgres
uv sync --locked --extra postgres
SPECVANIA_TEST_DATABASE_URL='postgresql+psycopg://specvania:specvania@localhost:5432/specvania' .venv/bin/pytest -q
docker compose down
```

The compose credentials above are for local development only. SQLite is the
default ledger and needs no Docker. See the
[validation gates](.agent/validation-gates.md) for the authoritative applicability.

The CI matrix runs G1–G4 on Linux/macOS with Python 3.12/3.13. Separate jobs run
the ledger contract and CLI end-to-end flow against Postgres, and build and
inspect the wheel before `uvx --from dist/*.whl specvania replay demo --help`.
The zip inspection test is offline; without a built wheel it skips with a
visible reason. Release CI separately builds the optional desktop installers.

An applicable gate must be green before a room is cleared. If Docker or Node
is unavailable, report the missing check and await CI evidence. CI success,
native visual behavior and owner checks cannot be inferred from static tests.
Never weaken or skip tests to obtain green gates.

## Handoff

Report with execution protocol §5: CLEARED or BLOCKED, every changed file,
gate results, exact checklist evidence, deviations, open questions and the next
room with open doors. Prepare boss evidence for the owner. Commit once per
approved room as `feat(Txx): <room title in lowercase>` (one commit per room) and
never push; stop after that room. If the executor cannot write `.git`, leave the working tree
for the reviewer to rerun gates in a clean checkout and commit after approval.

## Sponsor

Support maintenance and development through
[GitHub Sponsors — @rotirotirafa](https://github.com/sponsors/rotirotirafa).
Sponsorship is optional; bug reports, documentation and room proposals are also welcome.
