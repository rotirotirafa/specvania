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

### DEV-010 · 2026-10-13 · T05
**What changed:** Nothing; this records the one-time manual check of D4's default model ids. `specvania doctor`, run with the owner's real `NVIDIA_API_KEY` against `https://integrate.api.nvidia.com/v1` from a fresh `specvania init` config, reported all nine role entries as found in `/v1/models` (mage `moonshotai/kimi-k3`, `z-ai/glm-5.3`, `nvidia/nemotron-3-ultra-550b-a55b`; warrior `z-ai/glm-5.3`, `moonshotai/kimi-k2.6`, `deepseek-ai/deepseek-v4.1-flash`; paladin `nvidia/nemotron-3-ultra-550b-a55b`, `deepseek-ai/deepseek-v4.1-flash`, `z-ai/glm-5.3-flash`), ledger connected at migration 0002, exit 0.
**Why:** T05 requires the D4 ids to be checked against the real catalog once and the result recorded here.
**Impact:** None to D4. Catalog presence does not prove quality or callability; T23 validates those.
**Approved by:** owner, 2026-10-13 (check run at the owner's request).

### DEV-009 · 2026-10-12 · T17
**What changed:** Optional `run_started.lives` and `run_started.prices` carry the configured lives limit and reference model rates/currency to the HUD. The engine reads T12's local pricing file; absent metadata uses D13's three lives and tokens only. RunState preserves this metadata and the latest dialogue event for reconnects. Stamina is an explicitly labeled estimate: a cooldown empties the shared bar, which recovers linearly over its duration; overlapping cooldowns use the lowest recovery fraction.
**Why:** Existing events omit the configurable lives limit, pricing and limiter availability. The reviewer authorized lives/prices wiring; the owner authorized deterministic cooldown-derived stamina. Round-trip, engine and Node tests cover old streams and HUD projections.
**Impact:** T17, run/events.py, run/engine.py, run/state.py, D6, D11, D13 and S9. No new dependency, event type or CLI edit. Prices in the event are reference rates only; the HUD presents no invented price.
**Approved by:** reviewer under the owner's wiring delegation.

### DEV-008 · 2026-10-12 · T13
**What changed:** Within a run, repeated failed boss evaluations reuse one bug per area/scenario without another `bug_opened`. A later passing evaluation closes that scenario's still-open bug from the same run as fixed with `fixed_by_run` and emits `bug_closed`; choosing skip keeps the bug open.
**Why:** The T13 reviewer found duplicate monsters after repeated failures and stale monsters after a successful retry. Fake-provider tests cover fail/retry/fail, fail/retry/pass and fail/skip.
**Impact:** T13 engine bug hooks, D17, S14 and T17 Bestiary. No schema migration or event field/type change.
**Approved by:** owner, 2026-10-12

### DEV-007 · 2026-10-12 · T13
**What changed:** CLI `bug add` and `bug close --wontfix` write only the bugs table through the Ledger API, without events. After `world_loaded`, runs emit `bug_opened` for every open bug of their feature. The optional `spec_written.from_bug_id` field records the source bug in the feature's plan-run events; a finished run with every room cleared and none skipped closes each still-open linked bug with `fixed_by_run` and emits `bug_closed`. No migration.
**Why:** Events require a run and CLI bug commands run outside one. The reviewer approved persisting the feature/source bug link in existing plan-run events under the owner's wiring delegation.
**Impact:** bugs.py, planner (T07), events, engine (T11), map/Bestiary (T17), D17 and S14. Existing event streams remain readable because `from_bug_id` defaults to None.
**Approved by:** owner, 2026-10-12 (CLI events and run-start emission); reviewer under the owner's wiring delegation (optional source bug field and DEV-007 numbering).

### DEV-006 · 2026-10-12 · T11
**What changed:** Skipping a room saves its uncommitted tracked edits, deletions and non-ignored untracked files in a named local Git stash before continuing. The stable stash id is included in `room_skipped.reason`. Abort leaves the working tree in place for inspection.
**Why:** The T11 reviewer identified that skipped partial work contaminated the next room's Paladin diff and commit. Temporary-repository tests cover review failure and both Warrior and Paladin router errors, stash recovery and abort preservation.
**Impact:** T11 engine/GitOps, D7, S4 and S7. No event types or fields change; stash metadata stays within the existing 200-character reason limit.
**Approved by:** owner, 2026-10-12

### DEV-005 · 2026-10-12 · T11
**What changed:** A failed boss pause offers retry / skip / abort; skip continues to the next area with the boss recorded as failed, no new event type. Retry evaluates the same area diff with fresh gate results; abort finishes the run with status aborted.
**Why:** S12/T11 say a failed scenario pauses the run but do not define the choices.
**Impact:** Engine (T11), bugs (T13), map boss HP (T17).
**Approved by:** owner, 2026-10-12.

### DEV-004 · 2026-10-12 · T20
**What changed:** Rejected approval messages receive a non-secret `approval_rejected` WebSocket reply and leave the watching connection open. The view cookie is named `specvania_view_<port>` using the actual bound port. State/event transport envelopes optionally include a transient `approval_request` containing the full command and cwd; connecting during a pending approval restores its choice box without repeating a notification. Ledger events and their summary limits are unchanged.
**Why:** The owner authorized the T14 rejection/cookie findings in T20. Desktop approval must display the command it authorizes, including text beyond the ledger's 200-character summaries, and must remain available when a window connects during a pending request. `tests/test_desktop_approval.py` proves rejection continuity, cookie isolation, broker execution/cancellation, full prompt delivery and reconnect behavior; `tests/desktop/approval.test.cjs` proves the matching UI behavior.
**Impact:** T14/T20 UI transport, D8 and D16. Live callers register `UIServer.desktop_responder` in their ApprovalBroker alongside TerminalResponder; the existing explicit approval_responder hook remains supported. Tokens stay out of replies, prompts, ledger events and logs.
**Approved by:** owner, 2026-10-12

### DEV-003 · 2026-10-12 · T07
**What changed:** Plan executions are stored as runs with kind='plan' via migration 0002 adding runs.kind. Successful plan runs have an empty branch, status finished and finished_at set; failed plans leave drafts without ledger records. Mage outputs accept one JSON object inside fences or prose, and invalid Clarify output receives up to two validation-error repairs before a clean failure.
**Why:** Events require a run and the glossary defines a run as an execution of run or plan. The T07 reviewer requested shared extraction and bounded Clarify repairs for real-model formatting robustness.
**Impact:** Ledger schema/API, T07, shared provider/jsonout.py and Paladin import wiring; later history/summary/status should label or filter plan runs (T12). Paladin behavior and event fields remain unchanged.
**Approved by:** owner, 2026-10-12 (plan runs); reviewer under the owner's wiring delegation (output robustness).

### DEV-002 · 2026-10-12 · T10
**What changed:** Paladin room and boss outputs accept one JSON value inside fences or prose and receive up to two validation-error repair calls. Room checklist matching normalizes case, whitespace, checkbox markup and trailing punctuation, uses positional fallback for unmatched items when counts agree, ignores extras, and marks missing items not ok while preserving the room's original text and order. Gate summaries use the output tail; `GateRunner.run` returns transient `GateExecution(result, output_tail)` values bounded by `limits.observation_kb`, and failed-gate tails reach the Paladin and returned respawn feedback. Event fields and their 200-character limits are unchanged.
**Why:** The T10 reviewer identified real-model formatting failures, brittle checklist text matching and loss of failure diagnostics at the end of gate output. Fake-provider tests prove extraction, repair limits and matching; subprocess tests prove tail preservation and bounded events.
**Impact:** T10 reviewer/gates APIs and Paladin prompts; later T11 should pass GateExecution values to the reviewer and use returned feedback for respawn. This extends the D3 repair policy to the Paladin and retains D8 gate authorization and D13 observation bounds.
**Approved by:** owner, 2026-10-12

### DEV-001 · 2026-10-12 · T14
**What changed:** httpx2 added to the allowed dev dependencies.
**Why:** Starlette 1.7 TestClient requires it, needed to test HTTP and WebSocket in-process without opening sockets, which the socket guard forbids.
**Impact:** docs/conventions.md dev list, pyproject.toml, T14 and later UI rooms.
**Approved by:** owner, 2026-10-12
