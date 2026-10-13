# Contributing to specvania

Humans and coding agents follow the same Spec-Driven Development contract.
Start with [AGENTS.md](AGENTS.md) and read, in its listed order, the
[execution protocol](.agent/execution-protocol.md),
[validation gates](.agent/validation-gates.md), [domain vocabulary](docs/domain.md),
[conventions](docs/conventions.md), [spec](docs/spec.md), [rooms](docs/tasks.md)
and [accepted deviations](.agent/deviations.md). Decisions D1–D17 are binding.

Work on one authorized room at a time, with its doors open. Announce the room,
checklist and expected files. Keep changes within its `files:` line and trivial
wiring; write evidence for each checklist item and tick only items you proved.
Do not change completed rooms or add dependencies outside the conventions'
allowed list without following the protocol's stop rules S-1–S-7.

Log any change to spec intent in `.agent/deviations.md` using its template and
the next free DEV number, newest first. Describe what changed, why, impact and
approval status; use `Approved by: pending` until approved. Do not silently edit
the spec. When blocked, report the question, options and recommendation.

## Checks and review

From a clone with uv installed:

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
uv run specvania lint docs/
```

Tests use the fake provider and fake API keys. The socket guard stays active:
only the configured `SPECVANIA_TEST_DATABASE_URL` host can use the network.
Temporary repositories are used for Git tests; never use a real key in tests.

| Gate | Evidence required |
|---|---|
| G1 | `ruff check .` has no errors |
| G2 | `ruff format --check .` needs no reformatting |
| G3 | `pyright` reports zero errors |
| G4 | `pytest -q` passes with the socket guard active |
| G5 | Each ticked checklist item maps to a test, command or screenshot in the report |
| G6 | T15–T21 and T23 have the required visual screenshot or GIF |
| G7 | From T06 on, `specvania lint docs/` prints OK |
| G8 | The owner reviews each area's boss scenarios at the end of that area |
| G9 | Ledger, engine and history rooms, including T22, pass against Postgres |
| G10 | T19–T21 pass `npm --prefix apps/desktop run check` |

For G9, start the optional local ledger and run the full suite:

```sh
docker compose up -d --wait postgres
uv sync --locked --extra postgres
SPECVANIA_TEST_DATABASE_URL='postgresql+psycopg://specvania:specvania@localhost:5432/specvania' uv run pytest -q
docker compose down
```

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
approved room as `feat(Txx): <room title in lowercase>` and never push; stop
after that room. If the executor cannot write `.git`, leave the working tree
for the reviewer to rerun gates in a clean checkout and commit after approval.
