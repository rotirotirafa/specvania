# specvania

**A spec-driven coding agent where your spec becomes a metroidvania map.**

A party of open models — a **Mage** that plans, a **Warrior** that codes and a **Paladin** that reviews — explores your spec room by room. Every task is a room, every dependency is a door, every acceptance scenario is a boss, every open bug is a monster. You watch the run on a pixel-art map, in your browser or in a desktop game window, live or as a replay.

> 🚧 **Status: spec first, built in public.** Nothing runs yet. The whole project is planned in [`docs/spec.md`](docs/spec.md) and [`docs/tasks.md`](docs/tasks.md), and it is being built with the same Spec-Driven Development method it implements. Its own task list will be the first map it renders.

---

## Why

- **Most coding agents take a prompt, not a contract.** specvania is SDD-native: Clarify → spec with BDD scenarios → tasks with checklists → execution one room at a time → review against the checklist → boss fight against the scenarios.
- **Progress is usually a scrolling log.** Here the map *is* the progress: what is done, what is locked, where the party is, what it cost.
- **Agents forget.** specvania keeps a ledger of every run, feature, spec version and bug, so the next run starts from history instead of from zero.
- **Open models are cheap to try, hard to orchestrate.** specvania uses a different model per role, a shared rate limiter, automatic fallback on `429`, and per-model metrics you can publish.

## How SDD maps to the game

| SDD | specvania | On the map |
|---|---|---|
| Spec | World | The whole map |
| Task group | Area | A region |
| Task | Room | A room, locked until its doors open |
| Dependency | Door | Opens when the previous room is cleared |
| Checklist passes review | Room cleared | Room lights up, one git commit |
| Acceptance scenarios | Boss | One HP segment per scenario |
| Planner / Coder / Reviewer | Mage / Warrior / Paladin | The party |
| Rate-limit budget | Stamina | Bar under each character |
| `429` backoff | Cooldown | Character rests |
| Review rejected | Respawn | Back to the room entrance, with feedback |
| Open bug | Monster | Waits in its room; listed in the Bestiary |
| Project history | Ledger | The save file |
| Cost-equivalent | Coins | Only when you configure prices, always with a source |

## Planned usage

```bash
specvania init                       # config in your git repo
specvania doctor                     # check key, models and ledger
specvania plan "add invoices with PDF export" --ui
specvania run .specvania/specs/invoices --ui
specvania history                    # features, spec versions, runs, open bugs
specvania bug list                   # the Bestiary
specvania replay demo                # no API key needed
```

Default provider: the [NVIDIA API catalog](https://build.nvidia.com) (OpenAI-compatible, free tier for prototyping). Any OpenAI-compatible endpoint works by changing `base_url`.

## Architecture

```
specvania (Python) ── Mage / Warrior / Paladin ── router + rate limiter ── OpenAI-compatible API
      │
      ├── engine ── git (one branch per run, one commit per room, never pushes)
      ├── ledger ── SQLite by default, Postgres for teams (docker compose)
      └── UI server (127.0.0.1) ── same web app in a browser tab or the Electron desktop window
```

- **Zero setup by default:** `uvx specvania` with SQLite. No Docker, no Node.
- **Team mode:** set `SPECVANIA_DATABASE_URL` and use the bundled `docker-compose.yml` for Postgres.
- **Desktop window (optional):** a game window with native notifications, where you can also approve shell commands.

## Safety by design

- Runs on its own branch, one commit per room, **never pushes**.
- Every shell command proposed by a model needs your approval, in the terminal or the desktop window.
- File tools cannot leave the repository and never read `.env`, keys or certificates.
- The UI only listens on `127.0.0.1` and requires per-run tokens; only the desktop window can approve commands.
- API keys and tokens are never written to the ledger, logs or events.

## Roadmap

Six areas, twenty-three rooms — see [`docs/tasks.md`](docs/tasks.md).

| Area | What it unlocks |
|---|---|
| A1 · Foundations | CLI, config, events, ledger (SQLite + Postgres), provider with rate limit and fallback |
| A2 · Spellbook | World format, `lint`, Mage with Clarify |
| A3 · Dungeon | Tools, Warrior loop, Paladin review, engine, history, bugs |
| A4 · Map | Live map, party HUD, bosses, monsters, replay |
| A5 · Desktop | Electron window, approvals and notifications, packaged releases |
| A6 · Ship | Packaging, docs, Postgres end-to-end, a real recorded run |

## Contributing

Read [`AGENTS.md`](AGENTS.md) first. Humans and coding agents follow the same contract: [`.agent/execution-protocol.md`](.agent/execution-protocol.md).

## License

[MIT](LICENSE). Not affiliated with NVIDIA. All art is original.
