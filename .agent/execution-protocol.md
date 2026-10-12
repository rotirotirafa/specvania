# Execution protocol

How any coding agent (Codex, Claude Code, Devin, or specvania itself later) executes rooms in this repository. This file is the contract between whoever plans and whoever executes. If anything here conflicts with a prompt, this file wins; ask the owner.

## 1. Read before the first room

1. `AGENTS.md` / `CLAUDE.md` (bindings, nothing else)
2. This file
3. `docs/domain.md` — vocabulary; use these words in code and messages
4. `docs/conventions.md` — stack, layout, rules
5. `docs/spec.md` — what and why; decisions D1–D17 are binding
6. `docs/tasks.md` — the rooms
7. `.agent/deviations.md` — what already changed and why

## 2. The loop — one room at a time

1. **Pick** the first room in `docs/tasks.md`, by id, that is not checked and whose `depends_on` rooms are all done. If the owner named a room, take that one (and stop if its doors are not open).
2. **Announce**: room id, title, the checklist, and the files you expect to touch. If you expect to touch files outside the room's `files` line beyond trivial wiring (imports, `cli.py` registration), stop and ask.
3. **Implement** only what the room asks. Write the tests that prove each checklist item first, or alongside the code.
4. **Run the gates** in `.agent/validation-gates.md`. Fix and re-run. Never weaken a test or a gate to make it pass.
5. **Tick** each checklist item in `docs/tasks.md` only when you have evidence (test name, command output or screenshot).
6. **Commit** once per room: `feat(T06): world format parser and lint` (Conventional Commits, room id in scope). Do not push.
7. **Report** with the template in §5 and **stop**. Start the next room only if the owner says so (e.g. "continue", "do A2").

## 3. Stop rules — stop and ask the owner when

- S-1. The spec is ambiguous in a way that changes behavior a user would notice.
- S-2. A decision D1–D17 seems wrong or blocks the room.
- S-3. You need a dependency that is not in the allowed list in `docs/conventions.md`.
- S-4. The same gate fails twice after real fixes.
- S-5. A checklist item cannot be met as written (e.g. it depends on a later room).
- S-6. You would need to touch a room that is already done.
- S-7. Anything needs a real API key, network access in tests, a push, or a destructive git command.

When stopping, write the question, the options you see and your recommendation. Do not guess.

## 4. Deviations

Any change to the spec's intent — even a good one — is logged in `.agent/deviations.md` before the commit, with the format defined there. Small renames inside a room's own files are not deviations. Changing an event name, a CLI flag, a file format or a default value is.

## 5. Room report template

```markdown
## Room T06 · World format: parser and lint — CLEARED | BLOCKED

**Files changed:** list
**Gates:** G1 ✔ · G2 ✔ · G3 ✔ · G4 ✔ (N tests) · G6 n/a
**Checklist evidence:**
- item → test name / command / screenshot
**Deviations:** none | link to entry in .agent/deviations.md
**Open questions for the owner:** none | list
**Next room with open doors:** T07
```

## 6. Never

- Push, force-push, rewrite history or delete branches.
- Delete or skip tests to get green gates.
- Add network calls to tests, or read a real API key in tests.
- Edit `docs/spec.md` silently. Spec changes go through a deviation entry and the owner.
- Copy art, characters, names or code from existing games or from projects with incompatible licenses.
