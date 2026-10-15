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

### DEV-019 · 2026-10-15 · T14/T05, T34
**What changed:** Animated thinking dots with elapsed whole seconds appear in the party panel and the Clarify working text ahead of v0.4 D29. Reduced motion keeps a static ellipsis while seconds count; Watch demo uses recorded event time.
**Why:** The owner's T23 dogfood report: multi-minute model waits looked frozen because the UI changed only when a step completed. The owner explicitly chose a simple running indicator before launch on 2026-10-15.
**Impact:** T14/T05 party HUD, T34 Clarify screen and i18n. No new event, control message or server change; D28's dialogue box and D29's full thinking line remain for v0.4.
**Approved by:** owner, 2026-10-15

### DEV-018 · 2026-10-15 · T19
**What changed:** T19's frameless 16:9 window becomes a standard, freely resizable desktop window with native controls: macOS uses `titleBarStyle: "hiddenInset"` over the pixel title bar, with space for the traffic lights; Windows and Linux use a native frame. The aspect lock is removed, the minimum stays 960×540, and size, position, maximized/full-screen state and page zoom are validated and saved in `userData/desktop-window.json`, with a centered default for an unreachable saved position. An explicit application/Edit/View/Window menu supplies native editing, zoom and window roles; packaged builds have no reload or developer-tools menu items.
**Why:** The owner's T23 dogfood report found no minimize or maximize/full-screen buttons and no configurable window size. On 2026-10-15 the owner explicitly chose a standard macOS window.
**Impact:** T19 window setup and tests; T33 pixel title bar padding/drag region and preload allowlist (one added `platform` string); T38 packaged menu behavior. Desktop regression tests cover per-platform controls, free resize at 1280×1000 and 1920×800, state/zoom persistence, invalid/off-screen state and menu roles. No token is stored in window state; no task checklist is changed.
**Approved by:** owner, 2026-10-15

### DEV-017 · 2026-10-14 · T20
**What changed:** when denying a model-proposed command in the desktop window the player may add an optional reason (≤200 characters); it is stored in `approval_answered.reason` and the Warrior's observation becomes "denied by user: <reason>". Terminal answers are unchanged.
**Why:** the owner's T23 dogfood run — the player denied a syntactically corrupted script and had no way to tell the Warrior why.
**Impact:** D8 (approval flow) and T20 desktop approval UI/transport, T08 broker and run_shell observation, T03 `approval_answered` (new optional field; old records stay valid), i18n.
**Approved by:** owner, 2026-10-14

### DEV-016 · 2026-10-14 · T05
**What changed:** new event `model_requested` (role, model, room_id when present) emitted by the router immediately before every provider request; the HUD shows that role as thinking until the matching `model_call`.
**Why:** the owner's T23 dogfood run — `action` precedes the tool and `model_call` arrives only after a call completes, so the Warrior showed its last action as "acting" during a 284-second model wait.
**Impact:** T03 events, T05 router, T17 party HUD (state.py and party.js mapping), status-line i18n. T11/T27: the stamina check and studio cancellation now run in the router's `before_request` hook, before `model_requested` is recorded (replacing the engine's `_BudgetProvider` wrapper), so a stamina pause or a stop never leaves a request without its call. Call counts, latency, cost and summaries still come only from `model_call`; existing recordings and the synthetic demo stay valid.
**Approved by:** owner, 2026-10-14

### DEV-015 · 2026-10-14 · T27
**What changed:** Stopping a run from the studio raises `RunCancelled`; the engine finishes the run as aborted with no `run_paused`, no synthetic `model_call` (HTTP 400) and no `approval_answered` denial for a pending approval.
**Why:** The owner's T23 dogfood run recorded a fake "Provider call failed (HTTP 400); check provider configuration" pause and model call when the desktop app quit, which misled diagnosis.
**Impact:** T11 engine, T27 studio cancellation. `run_finished` now clears an unanswered approval from the folded run state (`state.py`) and from the server's transient `approval_request` on connect (`server.py`, T14/T20 projection). Event types and fields unchanged; aborted runs keep their working tree.
**Approved by:** owner, 2026-10-14

### DEV-014 · 2026-10-14 · T07
**What changed:** the Mage's Clarify/spec inputs and the Warrior's attempt context include `config.gates`; the spec and Warrior prompts require every room to leave the gates passing (tests live with the behavior they cover; the first room installs what the gates need).
**Why:** the owner's T23 dogfood run — the map put tests only in T02 while `uv run pytest -q` ran after T01; pytest exit 5 (no tests collected) failed T01 on every attempt, and attempt 1 also failed because pytest was not installed.
**Impact:** T07 planner and prompts, T09 executor/AttemptContext and Warrior prompt, T11 engine wiring. D8 unchanged (gates still run without approval; a Warrior-run gate command still needs approval). No event or config format change.
**Approved by:** owner, 2026-10-14

### DEV-013 · 2026-10-13 · T24
**What changed:** The desktop handshake timeout increases from 20 s to 60 s. Launch and Restart studio show a local, translated starting screen with elapsed seconds and a first-start hint after 10 s, replaced by the game on handshake or the error screen on failure/timeout.
**Why:** On the owner's machine, the reviewer measured `$SHELL -lic exit` taking up to 9.5 s under load and the full `$SHELL -lic 'exec specvania studio'` path taking 12.9–18.8 s. A fresh install also compiles bytecode on first start; three review sessions hit the 20 s limit.
**Impact:** T24 desktop startup and checklist wording; T33 timeout error text in both dictionaries and the local starting screen. The handshake format and token rules are unchanged.
**Approved by:** reviewer under the owner's wiring delegation, 2026-10-13

### DEV-012 · 2026-10-13 · T05
**What changed:** A provider HTTP 404 immediately advances the role to the next model for the rest of the room, resets consecutive failures, and emits `fallback` with reason `model unavailable (HTTP 404)` after the existing `model_call`, without provider backoff or a provider `cooldown`. A 404 on the last model raises `ChainExhaustedError`; other non-retryable statuses still raise unchanged.
**Why:** After the timed-out `z-ai/glm-5.3` Warrior calls in the owner's T23 dogfood run, `moonshotai/kimi-k2.6` returned HTTP 404 in 0.5 seconds (function not found), despite appearing in `/v1/models`. The router raised and paused the run instead of trying `deepseek-ai/deepseek-v4.1-flash`.
**Impact:** D5, T05 router and regression tests, engine pauses only after an unavailable chain is exhausted, and T17 HUD model updates through the existing fallback event. Per-room chain resets, shared rate limiting and 429/5xx retries remain unchanged.
**Approved by:** reviewer under the owner's wiring delegation, 2026-10-13

### DEV-011 · 2026-10-13 · T05
**What changed:** Provider requests use configurable `provider.timeout_s`, a positive finite float defaulting to 300 seconds, instead of the hard-coded 60-second SDK timeout. The setting also applies to injected SDK clients and is written by `specvania init`.
**Why:** During the owner's T23 dogfood run against `https://integrate.api.nvidia.com/v1`, three Warrior calls to `z-ai/glm-5.3` failed at 60.3 seconds with synthetic status 599, while a tiny probe answered in 1.6 seconds. Whole-file Warrior replies and reasoning tokens need more time; `moonshotai/kimi-k3` produced 1,227 output tokens in 26 seconds during planning.
**Impact:** D2/D5, T05, provider config validation and SDK construction, init defaults and README key discovery. Router retries and rate limits remain unchanged; invalid timeouts are rejected without echoing input values.
**Approved by:** reviewer under the owner's wiring delegation, 2026-10-13

### DEV-010 · 2026-10-13 · T05
**What changed:** Nothing; this records the one-time manual check of D4's default model ids. `specvania doctor`, run with the owner's real `NVIDIA_API_KEY` against `https://integrate.api.nvidia.com/v1` from a fresh `specvania init` config, reported all nine role entries as found in `/v1/models` (mage `moonshotai/kimi-k3`, `z-ai/glm-5.3`, `nvidia/nemotron-3-ultra-550b-a55b`; warrior `z-ai/glm-5.3`, `moonshotai/kimi-k2.6`, `deepseek-ai/deepseek-v4.1-flash`; paladin `nvidia/nemotron-3-ultra-550b-a55b`, `deepseek-ai/deepseek-v4.1-flash`, `z-ai/glm-5.3-flash`), ledger connected at migration 0002, exit 0.
**Why:** T05 requires the D4 ids to be checked against the real catalog once and the result recorded here.
**Impact:** None to D4. Catalog presence does not prove quality or callability; T23 validates those.
**Approved by:** owner, 2026-10-13 (check run at the owner's request).

### DEV-009 · 2026-10-12 · T17
**What changed:** Optional `run_started.lives` and `run_started.prices` carry the configured lives limit and reference model rates/currency to the HUD. The engine reads T12's local pricing file; absent metadata uses D13's three lives and tokens only. RunState preserves this metadata and the latest dialogue event for reconnects. Stamina is an explicitly labeled estimate: a cooldown empties the shared bar, which recovers linearly over its duration; overlapping cooldowns use the lowest recovery fraction.
**Why:** Existing events omit the configurable lives limit, pricing and limiter availability. The reviewer authorized lives/prices wiring; the owner authorized deterministic cooldown-derived stamina. Round-trip, engine and Node tests cover old streams and HUD projections.
**Impact:** T17, run/events.py, run/engine.py, run/state.py, D6, D11, D13 and S9. No new dependency, event type or CLI edit. Prices in the event are reference rates only; the HUD presents no invented price. The cooldown-derived stamina estimate is superseded by D21 in spec v0.3 (T27); the lives, prices and dialogue metadata remain in effect. The original estimate above is retained as history.
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
