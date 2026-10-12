# Deviations log

Every change to the spec's intent, logged before the commit that introduces it. Newest first.

## Format

```markdown
### DEV-001 · 2026-10-20 · T04
**What changed:** one or two sentences.
**Why:** the reason, with evidence (error, measurement, link).
**Impact:** which decisions, scenarios, rooms or files are affected.
**Approved by:** owner | pending
```

## Entries

### DEV-003 · 2026-10-12 · T07
**What changed:** Plan executions are stored as runs with kind='plan' via migration 0002 adding runs.kind. Successful plan runs have an empty branch, status finished and finished_at set; failed plans leave drafts without ledger records. Mage outputs accept one JSON object inside fences or prose, and invalid Clarify output receives up to two validation-error repairs before a clean failure.
**Why:** Events require a run and the glossary defines a run as an execution of run or plan. The T07 reviewer requested shared extraction and bounded Clarify repairs for real-model formatting robustness.
**Impact:** Ledger schema/API, T07, shared provider/jsonout.py and Paladin import wiring; later history/summary/status should label or filter plan runs (T12). Paladin behavior and event fields remain unchanged.
**Approved by:** owner, 2026-10-12 (plan runs); reviewer under the owner's wiring delegation (output robustness).

### DEV-002 · 2026-10-12 · T10
**What changed:** Paladin room and boss outputs accept one JSON value inside fences or prose and receive up to two validation-error repair calls. Room checklist matching normalizes case, whitespace, checkbox markup and trailing punctuation, uses positional fallback for unmatched items when counts agree, ignores extras, and marks missing items not ok while preserving the room's original text and order. Gate summaries use the output tail; `GateRunner.run` returns transient `GateExecution(result, output_tail)` values bounded by `limits.observation_kb`, and failed-gate tails reach the Paladin and returned respawn feedback. Event fields and their 200-character limits are unchanged.
**Why:** The T10 reviewer identified real-model formatting failures, brittle checklist text matching and loss of failure diagnostics at the end of gate output. Fake-provider tests prove extraction, repair limits and matching; subprocess tests prove tail preservation and bounded events.
**Impact:** T10 reviewer/gates APIs and Paladin prompts; later T11 should pass GateExecution values to the reviewer and use returned feedback for respawn. This extends the D3 repair policy to the Paladin and retains D8 gate authorization and D13 observation bounds.
**Approved by:** pending

### DEV-001 · 2026-10-12 · T14
**What changed:** httpx2 added to the allowed dev dependencies.
**Why:** Starlette 1.7 TestClient requires it, needed to test HTTP and WebSocket in-process without opening sockets, which the socket guard forbids.
**Impact:** docs/conventions.md dev list, pyproject.toml, T14 and later UI rooms.
**Approved by:** owner, 2026-10-12
