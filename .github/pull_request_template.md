## What changed

<!-- Describe the problem, resulting behavior and evidence a reviewer can verify. -->

Room id (if any):

## Gates

Use [.agent/validation-gates.md](../.agent/validation-gates.md) to determine which
gates apply. Check only gates with passing evidence. For each not applicable gate,
leave it unchecked and give the reason below; distinguish pending checks from n/a.

- [ ] G1 — `ruff check .` passes (every room).
- [ ] G2 — `ruff format --check .` passes (every room).
- [ ] G3 — `pyright` reports zero errors (every room).
- [ ] G4 — `pytest -q` passes with the socket guard active (every room).
- [ ] G5 — Every ticked checklist item maps to exact test, command or screenshot evidence (every room).
- [ ] G6 — Attach a screenshot or short GIF for UI changes; visual gate for T15–T21, T23, T24, T29–T38 and T41.
- [ ] G7 — `specvania lint docs/` prints OK (from T06 onward).
- [ ] G8 — The owner confirms the area's boss scenarios (end of an area).
- [ ] G9 — The full suite passes with `SPECVANIA_TEST_DATABASE_URL` against Docker Postgres (ledger, engine or history changes).
- [ ] G10 — `npm --prefix apps/desktop run check` passes (T19–T21 and T24–T41).

Gate output, checklist evidence, screenshots and n/a reasons:

## Spec intent

Spec intent changed? → `.agent/deviations.md`

<!-- Answer yes or no. If yes, link the entry using the next free DEV number,
newest first, with what changed, why, impact and Approved by: pending until the
owner approves. Include changes to decisions, events, flags, formats or defaults. -->
