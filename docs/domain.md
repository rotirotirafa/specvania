# Domain glossary

One vocabulary for code, events, UI text, docs and posts. When a term exists here, use it — in identifiers, log lines and commit messages. The PT-BR column is for talks and posts; code is always in English.

## World

| Term | PT-BR | Meaning | In code |
|---|---|---|---|
| World | Mundo | A spec folder: `spec.md` + `tasks.md` | `World` |
| Scenario | Cenário | A BDD acceptance scenario in `spec.md`, id `S<n>` | `Scenario` |
| Area | Área | A group of rooms, id `A<n>`; has zero or more boss scenarios | `Area` |
| Room | Sala | One task, id `T<nn>`; has doors, covered scenarios, files and a checklist | `Room` |
| Door | Porta | A dependency between two rooms; opens when the source room is cleared | `Door(src, dst)` |
| Boss | Chefe | The evaluation of an area's boss scenarios after all its rooms are cleared | `boss_*` events |
| Checklist | Checklist | Binary items that must all be ok for a room to be cleared | `Room.checklist` |

## Party

| Term | PT-BR | Meaning | In code |
|---|---|---|---|
| Party | Grupo | The three roles working on a run | `Party` |
| Role | Papel | A job in the party; each has an ordered model chain | `Role` enum: `MAGE`, `WARRIOR`, `PALADIN` |
| Mage | Mago | Planner: Clarify, spec and tasks | `Role.MAGE` |
| Warrior | Guerreiro | Coder: executes a room through actions | `Role.WARRIOR` |
| Paladin | Paladino | Reviewer: reviews rooms and fights bosses | `Role.PALADIN` |
| Model chain | Cadeia de modelos | Ordered model ids a role may use; the first healthy one is active | `roles.<role>` in config |
| Fallback | Troca de modelo | Moving a role to the next model in its chain | `fallback` event |

## Run

| Term | PT-BR | Meaning | In code |
|---|---|---|---|
| Run | Execução | One execution of `specvania run` (or `plan`), id `<yyyymmdd-hhmmss>-<4 hex>`, stored in the ledger | `run_id` |
| Event | Evento | Immutable record of a state change, stored in the ledger; the source of truth | `Event` union |
| Attempt | Tentativa | One Warrior pass over a room | `attempt` (1-based) |
| Action | Ação | One JSON action returned by the Warrior | `Action` |
| Observation | Observação | The result of an action, sent back to the Warrior | `Observation` |
| Gate | Gate | A user-configured command run after each attempt | `gate_result` event |
| Review | Revisão | The Paladin's verdict on an attempt | `review` event |
| Respawn | Respawn | Starting a new attempt in the same room after a failed review | `respawn` event |
| Lives | Vidas | Max attempts per room | `limits.lives` |
| Cleared | Concluída | A room that passed review and was committed | `RoomState.CLEARED` |
| Paused | Pausada | A run waiting for the player (out of lives, failed boss, chain exhausted) | `run_paused` event |
| Replay | Replay | Re-projecting a recorded `events.jsonl` in the map | `specvania replay` |

## Ledger

| Term | PT-BR | Meaning | In code |
|---|---|---|---|
| Ledger | Registro (o "save") | The project's persistent history: SQLite by default, Postgres in team mode | `Ledger` |
| Project | Projeto | One repository known to the ledger (root path + origin URL) | `projects` table |
| Feature | Feature | A named world tracked over time (`.specvania/specs/<slug>`) | `features` table |
| Spec version | Versão da spec | An immutable snapshot of `spec.md` + `tasks.md`, identified by its sha256 | `spec_versions` table |
| Bug | Bug | A tracked problem, opened by the user, a skipped room or a failed boss scenario | `bugs` table, `Bug` |
| Monster | Monstro | How an open bug appears on the map | UI only |
| Bestiary | Bestiário | The panel and the `bug list` view of open bugs | UI and CLI |

## UI shells and security

| Term | PT-BR | Meaning | In code |
|---|---|---|---|
| Web app | App web | The single UI, served by the Python UI server | `ui/static/` |
| Browser tab | Aba do navegador | Shell that loads the web app with the view token only (watch) | `--ui=browser` |
| Desktop window | Janela desktop | Electron shell that loads the web app with view and control tokens (watch, approve, notify) | `apps/desktop/`, `--ui=desktop` |
| View token | Token de visualização | Random secret required to load the page and open the WebSocket | `view_token` |
| Control token | Token de controle | Random secret required to answer approvals; only the desktop window has it | `control_token` |
| Responder | Respondedor | A place that can answer an approval (terminal, desktop window) | `ApprovalResponder` |
| Studio | Estúdio | The long-lived server the desktop game starts and drives (D18) | `specvania studio`, `ui/studio.py` |
| Castle | Castelo | How a world is drawn: one floor per area, one chamber per room | `map/castle.js` |
| Handshake | Handshake | The one JSON line the studio prints on stdout with its port and tokens (D19) | studio stdout |

## Play (v0.4)

| Term | PT-BR | Meaning | In code |
|---|---|---|---|
| Step | Etapa | One of the six steps of the journey: Project, Idea, Clarify, Map, Expedition, End (D30); never an area or a room | `play/step.js` |
| Expedition | Expedição | A run as the game shows it: the party clearing the castle (step 5) | studio phase `running` |
| Dialogue box | Caixa de diálogo | The box at the bottom of the castle: who is acting, their latest line, the thinking line (D28) | `hud/dialogue-box.js` |
| Your turn | Sua vez | The dialogue box when the player must answer an approval, a Clarify question or a pause | narrator `turn` |
| Thinking line | Linha de pensamento | The box's last line while a role waits: a playful verb, the elapsed time and the model (D29) | `play/thinking.js` |
| Step help | Ajuda da etapa | The `?` card that explains the current step (D31) | `hud/help.js` |
| Cue | Efeito sonoro | A named sound effect played for an event or a UI action (D32) | `audio/cues.js` |

## Limits and metrics

| Term | PT-BR | Meaning | In code |
|---|---|---|---|
| Stamina | Stamina | Tokens left in the run's token budget (D21; until v0.2, the limiter's request budget) | `RunState` budget and spent |
| Token budget | Orçamento de tokens | Input + output tokens a run may spend before it pauses | `limits.token_budget` |
| Rest | Descansar | The paused-run choice that restores stamina (+25% of the original budget) | `stamina_restored` event |
| Cooldown | Recarga | A wait caused by 429/5xx backoff; the character rests (zzz) | `cooldown` event |
| Coins | Moedas | Cost-equivalent, only for models priced in `pricing.toml` | `coins` |
| Tokens | Tokens | Input/output tokens reported by the provider | `tokens_in`, `tokens_out` |

## Room states

`locked` → `open` → `active` → `cleared` | `failed` → (`active` again on retry) | `skipped`

## Bug states

`open` → `fixed` (closed by a fully cleared run planned with `--from-bug`) | `wontfix`

## Words to avoid

| Don't use | Use | Why |
|---|---|---|
| task (in code) | room | "task" collides with asyncio tasks and the task-list file name; `tasks.md` keeps its name |
| level, stage, quest | area / room for the castle; step for the journey (D30) | One metaphor only |
| agent (for a role) | role, or the role name | Every role is an agent; be specific |
| planner / coder / reviewer (in UI text) | Mage / Warrior / Paladin | Code may alias them in docstrings, UI never mixes |
| retry (for review failure) | respawn | "retry" is reserved for provider-level retries and the paused-run option |
| price, cost (without source) | coins, with source | Never show a number without its `source_url` |
| session | run | One word for one execution; "session" is ambiguous with HTTP and UI sessions |
| issue, ticket | bug | One word for tracked problems |
| database, DB (in domain code) | ledger | The ledger is the domain concept; SQLite and Postgres are adapters |
