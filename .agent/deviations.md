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

### DEV-001 · 2026-10-12 · T14
**What changed:** httpx2 added to the allowed dev dependencies.
**Why:** Starlette 1.7 TestClient requires it, needed to test HTTP and WebSocket in-process without opening sockets, which the socket guard forbids.
**Impact:** docs/conventions.md dev list, pyproject.toml, T14 and later UI rooms.
**Approved by:** owner, 2026-10-12
