# Validation gates

Automated gates run after every room. The human gate runs at the end of every area (the boss). Gates G1–G4 exist from T01 on; before T01 is cleared there is nothing to run.

| Gate | Command / action | When | Passes when |
|---|---|---|---|
| G1 | `uv run ruff check .` | every room | no errors |
| G2 | `uv run ruff format --check .` | every room | no files to reformat |
| G3 | `uv run pyright` | every room | 0 errors |
| G4 | `uv run pytest -q` | every room | all tests pass, socket guard active |
| G5 | Checklist evidence | every room | every ticked item points to a test, command output or screenshot in the report |
| G6 | Visual check | rooms T15–T21, T23, T24 and T29–T38 | screenshot or short GIF of the relevant screen attached to the report (`sprites.html` for T15 and T29; `uv run specvania replay demo` once T18 exists; the desktop window for T19–T21, T24 and T29–T38) |
| G7 | `uv run specvania lint docs/` | every room from T06 on | prints OK |
| G8 | Owner review against the area's boss scenarios | end of each area | owner confirms each boss scenario in the area with the evidence; findings become new rooms or deviations |
| G9 | `docker compose up -d postgres` then `SPECVANIA_TEST_DATABASE_URL=postgresql+psycopg://specvania:specvania@localhost:5432/specvania uv run pytest -q` | rooms that touch `ledger/`, the engine or `history` (T04, T07, T11, T12, T13, T22, T24, T25, T27) | all tests pass, including the Postgres variants |
| G10 | `npm --prefix apps/desktop run check` | rooms T19–T21 and T24–T38 | the `check` script (syntax check of main and preload, the Node tests in `tests/desktop/`) passes |

## Rules

- A room is not cleared while any applicable gate is red.
- If a gate is flaky, fix the flakiness in the same room or stop (stop rule S-4). Do not retry until green.
- G9 and G10 need Docker and Node locally; if they are missing, report it and let CI run them — do not mark the room cleared until CI is green.
- G8 is the only gate that needs the owner. Prepare it: list each boss scenario, how it was verified, and what is not covered yet.
