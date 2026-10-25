"""Pure report projections and guarded local artifacts, without terminal output."""

import json
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from specvania.config import Config, ProviderConfig, resolve_api_key
from specvania.ledger.repo import Ledger, Run
from specvania.pricing import Pricing, load_pricing
from specvania.run.events import Event, FrozenModel, RedactionGuard, SecretLeakError
from specvania.run.jsonl import encode_events
from specvania.run.state import ModelMetrics, RoomState, fold_events


class SummaryError(Exception):
    """A report or artifact cannot be produced; the player can fix its inputs."""


class RoomSummary(FrozenModel):
    room_id: str
    attempts: int
    result: RoomState
    commit: str | None


class RunSummary(FrozenModel):
    run_id: str
    kind: Literal["run", "plan"] = "run"
    status: str
    feature: str | None
    models: tuple[ModelMetrics, ...]
    rooms: tuple[RoomSummary, ...]
    calls: int
    tokens_in: int
    tokens_out: int
    rooms_cleared: int | None


def summarize(
    events: Iterable[Event],
    *,
    kind: Literal["run", "plan"] = "run",
    run_id: str = "",
    status: str | None = None,
) -> RunSummary:
    recorded = tuple(events)
    state = fold_events(recorded)
    attempts = Counter(e.room_id for e in recorded if e.type == "attempt_started")
    rooms = (
        tuple(
            RoomSummary(
                room_id=r.id, attempts=attempts[r.id], result=r.state, commit=r.commit
            )
            for r in state.rooms
        )
        if kind == "run"
        else ()
    )
    return RunSummary(
        run_id=state.run_id or run_id,
        kind=kind,
        status=status or state.status,
        feature=state.feature,
        models=state.models,
        rooms=rooms,
        calls=sum(m.calls for m in state.models),
        tokens_in=sum(m.tokens_in for m in state.models),
        tokens_out=sum(m.tokens_out for m in state.models),
        rooms_cleared=sum(r.result == RoomState.CLEARED for r in rooms)
        if kind == "run"
        else None,
    )


def ledger_summary(ledger: Ledger, run_id: str) -> RunSummary:
    run = ledger.get_run(run_id)
    return summarize(
        ledger.list_events(run_id), kind=run.kind, run_id=run.id, status=run.status
    )


def report_secrets(
    config: Config, environ: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    environment = os.environ if environ is None else environ
    names = (
        config.ledger.database_url_env,
        "SPECVANIA_DATABASE_URL",
        "SPECVANIA_VIEW_TOKEN",
        "SPECVANIA_CONTROL_TOKEN",
    )
    return (
        *resolve_api_key(config.provider, environment).secrets,
        *resolve_api_key(ProviderConfig(), environment).secrets,
        *(environment[name] for name in names if environment.get(name)),
    )


def validate_report(text: str, secrets: Sequence[str]) -> None:
    """Reject the whole output before any write/print, including JSON escapes."""
    for secret in secrets:
        if secret and any(
            value in text
            for value in (
                secret,
                json.dumps(secret, ensure_ascii=True)[1:-1],
                json.dumps(secret, ensure_ascii=False)[1:-1],
            )
        ):
            raise SecretLeakError("Report rejected: contains a registered secret")


def _cell(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def render_summary(
    summary: RunSummary,
    pricing: Pricing | None = None,
    *,
    secrets: Sequence[str] = (),
) -> str:
    prices = pricing if pricing is not None else Pricing()
    # Validate before Markdown escaping can split a printable API-key value.
    validate_report(summary.model_dump_json(), secrets)
    validate_report(prices.model_dump_json(), secrets)
    cleared = summary.rooms_cleared if summary.rooms_cleared is not None else "n/a"
    lines = [
        f"# Run {_cell(summary.run_id)}",
        "",
        f"Kind: {summary.kind}",
        f"Status: {summary.status}",
        f"Feature: {_cell(summary.feature or '—')}",
        f"Rooms cleared: {cleared}",
        f"Calls: {summary.calls}",
        f"Tokens in: {summary.tokens_in}",
        f"Tokens out: {summary.tokens_out}",
        "",
        "## Models",
        "",
        "| Model | Calls | Tokens in | Tokens out | 429s | Fallbacks | "
        "Latency (s) | Coins |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model in summary.models:
        price = prices.for_model(model.model)
        coins = (
            "not priced"
            if price is None
            else (
                f"{price.coins(model.tokens_in, model.tokens_out):f} {price.currency}; "
                f"source: {price.source_url}; checked: {price.checked_on.isoformat()}"
            )
        )
        lines.append(
            f"| {_cell(model.model)} | {model.calls} | {model.tokens_in} | "
            f"{model.tokens_out} | {model.rate_limits} | {model.fallbacks} | "
            f"{model.latency:g} | {_cell(coins)} |"
        )
    if summary.kind == "run":
        lines.extend(
            [
                "",
                "## Rooms",
                "",
                "| Room | Attempts | Result | Commit |",
                "| --- | --- | --- | --- |",
            ]
        )
        for room in summary.rooms:
            lines.append(
                f"| {_cell(room.room_id)} | {room.attempts} | {room.result} | "
                f"{_cell(room.commit or '—')} |"
            )
    output = "\n".join(lines) + "\n"
    validate_report(output, secrets)
    return output


def _artifact(root: Path, run_id: str, filename: str) -> Path:
    if not run_id or run_id in (".", "..") or "/" in run_id or "\\" in run_id:
        raise SummaryError("Invalid run id for a local artifact.")
    runs = root.resolve() / ".specvania/runs"
    path = runs / run_id / filename
    if not path.resolve().is_relative_to(runs):
        raise SummaryError("Run artifacts must stay inside .specvania/runs.")
    return path


def _write(path: Path, text: str) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except (OSError, UnicodeError):
        raise SummaryError(
            "Cannot write run artifact; check permissions and paths."
        ) from None
    return path


def write_summary(
    root: Path,
    ledger: Ledger,
    run_id: str,
    *,
    config: Config | None = None,
    secrets: Sequence[str] = (),
) -> Path:
    registered = tuple(secrets) + report_secrets(config or Config())
    output = render_summary(
        ledger_summary(ledger, run_id),
        load_pricing(root / ".specvania/pricing.toml"),
        secrets=registered,
    )
    path = _artifact(root, run_id, "summary.md")
    validate_report(str(path), registered)
    return _write(path, output)


def export_run(
    root: Path,
    ledger: Ledger,
    run_id: str,
    guard: RedactionGuard,
    *,
    secrets: Sequence[str] = (),
) -> Path:
    output = encode_events(ledger.list_events(run_id), guard)
    path = _artifact(root, run_id, "events.jsonl")
    validate_report(output + str(path), secrets)
    return _write(path, output)


def latest_run(ledger: Ledger, project_id: str) -> Run:
    runs = [
        run
        for feature in ledger.list_features(project_id)
        for run in ledger.list_runs(feature.id)
    ]
    if not runs:
        raise SummaryError("No runs recorded for this project.")
    return max(runs, key=lambda r: (r.started_at, r.id))


def history_report(ledger: Ledger, project_id: str, slug: str | None = None) -> str:
    features = ledger.list_features(project_id)
    bugs = ledger.list_bugs(project_id)
    if slug is None:
        lines = ["Feature | Status | Spec versions | Runs | Open bugs"]
        for feature in sorted(features, key=lambda f: f.slug):
            lines.append(
                f"{_cell(feature.slug)} | {_cell(feature.status)} | "
                f"{len(ledger.list_spec_versions(feature.id))} | "
                f"{len(ledger.list_runs(feature.id))} | "
                f"{sum(b.feature_id == feature.id for b in bugs)}"
            )
        if not features:
            lines.append("No features recorded for this project.")
        return "\n".join(lines)
    feature = next((feature for feature in features if feature.slug == slug), None)
    if feature is None:
        raise SummaryError("No feature record with that slug.")
    versions = ledger.list_spec_versions(feature.id)
    hashes = {v.id: v.sha256 for v in versions}
    lines = [
        f"Feature: {_cell(feature.slug)} | Status: {_cell(feature.status)}",
        "",
        "Spec versions: hash | created_at",
    ]
    lines.extend(f"{v.sha256} | {v.created_at.isoformat()}" for v in versions)
    lines.extend(
        ["", "Runs: id | kind | status | rooms cleared | tokens | spec version"]
    )
    for run in ledger.list_runs(feature.id):
        summary = ledger_summary(ledger, run.id)
        cleared = summary.rooms_cleared if summary.rooms_cleared is not None else "n/a"
        lines.append(
            f"{_cell(run.id)} | {run.kind} | {run.status} | {cleared} | "
            f"{summary.tokens_in + summary.tokens_out} | {hashes[run.spec_version_id]}"
        )
    lines.extend(["", "Open bugs: id | room | title"])
    lines.extend(
        f"{_cell(b.id)} | {_cell(b.room_id or '—')} | {_cell(b.title)}"
        for b in bugs
        if b.feature_id == feature.id
    )
    return "\n".join(lines)
