# Sample Todo API

A small, standalone FastAPI project for the T23 specvania dogfood run. This
folder is an input project, outside the specvania package and its wheel. Copy
it into a separate Git repository before planning or executing a feature.

The API uses Pydantic request/response models and an in-memory store per app.
Todos have an integer `id`, a trimmed `title` (1–200 characters), and a
`completed` flag, initially false. IDs increase and are not reused after deletion.
Data disappears when the app restarts; each server worker has its own store.

| Method | Path | Behavior |
|---|---|---|
| POST | `/todos` | Create with `{"title": "Buy milk"}`; return 201 |
| GET | `/todos` | List all todos in creation order |
| GET | `/todos/{id}` | Get a todo; return 404 if missing |
| PATCH | `/todos/{id}/complete` | Mark complete, idempotently; return 404 if missing |
| DELETE | `/todos/{id}` | Delete; return an empty 204, or 404 if missing |

Unknown create fields and invalid titles return 422. There are no due dates or
filters yet. Tests use FastAPI's TestClient in process, without opening sockets.

## Run the sample on its own

With Python 3.12+ and uv, from this folder (or its temporary copy):

```sh
uv run pytest -q
uv run uvicorn todo_api.app:create_app --factory --reload
```

The sample's own `pyproject.toml` declares runtime dependencies `fastapi` and
`uvicorn`, and dev dependencies `pytest` and `httpx2`. Pydantic is provided by
FastAPI. uv creates the sample's environment; specvania's dependencies and lock
file are separate. The app's API documentation is at `http://127.0.0.1:8000/docs`.

## Owner's real dogfood run

These steps require the owner's real NVIDIA key and provider access. Preparation
alone does not complete any T23 checklist item. Have `specvania` on PATH (see
the repository's installation instructions), uv available, and a Git author
configured. For T23's desktop recording, install the desktop app or set
`SPECVANIA_DESKTOP` to its launcher as described in `apps/desktop/README.md` in
the specvania repository. Bare `--ui` uses the generated config's `auto` mode
and falls back to a browser if the desktop app is unavailable; use
`--ui=desktop` to require the desktop window.

1. Copy the sample and make the first commit. Replace the repository path below.
   Testing before committing also creates the sample's `uv.lock`; commit it.
   If copying a previously tested sample, leave caches and `.venv` out of the copy.

   ```sh
   specvania_repo="/absolute/path/to/specvania"
   dogfood_dir="$(mktemp -d)"
   cp -R "$specvania_repo/examples/sample-todo-api/." "$dogfood_dir/"
   cd "$dogfood_dir"
   git init
   uv run pytest -q
   git add .
   git commit -m "initial todo API sample"
   specvania init
   ```

2. In the generated `.specvania/config.toml`, replace the **top-level**
   `gates = []` with the following, before any TOML table header. This is the
   owner's gate and runs without a shell approval prompt; model-proposed shell
   commands still require approval. No config template is shipped here.

   ```toml
   gates = ["uv run pytest -q"]
   ```

3. Set the key in the shell environment, run doctor, then commit configuration.
   Supply the actual key privately; never put it in the sample's files. Doctor
   must succeed before continuing: it checks configured models and the ledger.
   The default model chains still await T23's quality measurements and O1.

   ```sh
   export NVIDIA_API_KEY='<your real key>'
   specvania doctor
   git add .gitignore .specvania/config.toml
   git commit -m "configure specvania and pytest gate"
   specvania plan "add due dates and an overdue filter to the todo API"
   ```

4. Answer the Mage's Clarify questions, review the world, and use the actual
   spec directory printed by `plan` (the slug below is an example). Commit the
   world before executing: `run` requires a clean tree and a commit at HEAD.

   ```sh
   spec_dir=".specvania/specs/<slug printed by plan>"
   specvania lint "$spec_dir"
   git add "$spec_dir"
   git commit -m "specify due dates and overdue filter"
   git status --short
   specvania run "$spec_dir" --ui
   ```

   Capture the desktop run for the future repository README GIF. Review shell
   approvals, room results and boss results as they occur. specvania creates
   a `specvania/<run-id>` branch and commits each cleared room locally.

5. Use the execution run ID printed by `run`, rather than the earlier plan ID.
   Export and inspect its summary and status, even if it pauses or fails.

   ```sh
   run_id="<execution run id>"
   specvania export "$run_id"
   specvania status
   specvania status "$run_id"
   specvania replay ".specvania/runs/$run_id/events.jsonl"
   ```

   The exported file is `.specvania/runs/<run-id>/events.jsonl`; the generated
   summary is beside it at `.specvania/runs/<run-id>/summary.md`. Both live in
   the temporary sample repository and are gitignored by `specvania init`.
   Preserve the temporary repository until the recording and artifacts are saved.

## First feature idea and open decisions

Suggested prompt: **"add due dates and an overdue filter to the todo API"**.
Leave these choices for Clarify and record the agreed answers in the world:

- Is a due date optional? Is it a calendar date or a timezone-aware timestamp?
- What timezone and boundary define overdue: before today, or before now?
- Do completed todos appear in overdue results?
- How is filtering requested (for example `GET /todos?overdue=true`), and in
  what order are the results returned?
- Can a due date be changed or removed after creation? Are past dates accepted?

Keep the feature in memory and cover it with deterministic tests. The baseline
intentionally leaves these choices open so the first plan exercises Clarify.

## Real recorded run handoff (after the owner executes it)

In the specvania repository, the future destination is
`src/specvania/examples/real-run/`, containing **`events.jsonl` and `summary.md`**
from the actual execution. Copy those files from the temporary sample repository
only after the real run; do not manufacture a recording during preparation.
Include a README there clearly labeling it **a real recorded run**, with its
feature, run ID, outcome, model chains and recording date. Check replay using
the copied `events.jsonl` path; `replay demo` still refers to the synthetic demo.

Use that recording for the repository README GIF, and use the summary's calls,
tokens, latency, fallbacks and room outcomes to settle O1 with the owner in
`.agent/deviations.md`. A failed cycle must be documented honestly. The owner
and reviewer handle recording, artifacts, O1, checklist evidence and commits;
this sample preparation leaves all T23 checklist items unchecked.
