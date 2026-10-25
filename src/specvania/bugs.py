"""Bug lifecycle through the Ledger port; terminal rendering belongs to the CLI."""

import os
import re
from collections.abc import Callable
from pathlib import Path

from specvania.clock import Clock
from specvania.config import load_config, resolve_api_key
from specvania.ledger import LedgerError
from specvania.ledger.repo import Bug, Ledger
from specvania.run.events import BugClosed, BugOpened, Event, RedactionGuard
from specvania.run.gitops import GitError, GitOps


class BugError(Exception):
    """A bug command's inputs need attention from the player."""


def source_bug(ledger: Ledger, root: Path, bug_id: str) -> Bug:
    bug = ledger.get_bug(bug_id)
    if Path(ledger.get_project(bug.project_id).repo_root).resolve() != root.resolve():
        raise BugError("The bug belongs to another project.")
    if bug.status != "open":
        raise BugError("Plan from an open bug.")
    return bug


class Bugs:
    def __init__(self, ledger: Ledger, project_id: str) -> None:
        self.ledger = ledger
        self.project_id = project_id

    def add(
        self, title: str, *, feature: str | None = None, room: str | None = None
    ) -> Bug:
        if not title.strip():
            raise BugError("Give the bug a non-empty title.")
        if room is not None and re.fullmatch(r"T\d{2}", room) is None:
            raise BugError("Use a room id such as T01.")
        feature_id = None
        if feature is not None:
            matches = [
                f
                for f in self.ledger.list_features(self.project_id)
                if f.slug == feature
            ]
            if not matches:
                raise BugError(f"Feature {feature!r} does not exist in this project.")
            feature_id = matches[0].id
        return self.ledger.add_bug(
            self.project_id, title, feature_id=feature_id, room_id=room
        )

    def list(self, *, include_closed: bool = False) -> list[Bug]:
        return self.ledger.list_bugs(self.project_id, include_closed=include_closed)

    def close_wontfix(self, bug_id: str) -> Bug:
        bug = self.ledger.get_bug(bug_id)
        if bug.project_id != self.project_id:
            raise BugError("The bug belongs to another project.")
        if bug.status != "open":
            raise BugError("The bug is already closed.")
        return self.ledger.close_bug(bug_id, status="wontfix")

    def opened_event(self, bug: Bug, *, run_id: str, clock: Clock) -> BugOpened:
        feature = (
            self.ledger.get_feature(bug.feature_id).slug
            if bug.feature_id is not None
            else None
        )
        return BugOpened(
            ts=clock.now(),
            run_id=run_id,
            seq=0,
            bug_id=bug.id,
            title=bug.title[:200],
            body=bug.body[:200],
            feature=feature,
            room_id=bug.room_id,
            source=bug.source,
        )

    def close_from_plans(
        self, *, run_id: str, clock: Clock, emit: Callable[[Event], None]
    ) -> None:
        run = self.ledger.get_run(run_id)
        if run.kind != "run" or run.status != "finished":
            return
        bug_ids = {
            event.from_bug_id
            for plan_run in self.ledger.list_runs(run.feature_id)
            if plan_run.kind == "plan" and plan_run.status == "finished"
            for event in self.ledger.list_events(plan_run.id)
            if event.type == "spec_written" and event.from_bug_id is not None
        }
        for bug_id in sorted(bug_ids):
            bug = self.ledger.get_bug(bug_id)
            if bug.project_id != self.project_id or bug.status != "open":
                continue
            closed = BugClosed(
                ts=clock.now(),
                run_id=run_id,
                seq=0,
                bug_id=bug.id,
                status="fixed",
                fixed_by_run=run_id,
            )
            self.ledger.close_bug(bug.id, status="fixed", fixed_by_run=run_id)
            emit(closed)


def open_bug_ledger(
    root: Path, *, clock: Clock, guard: RedactionGuard
) -> tuple[Ledger, Bugs]:
    """The shared bug-list/studio project registration path (including migrations)."""
    git = GitOps(root)
    config = load_config(git.root / ".specvania/config.toml")
    for secret in (
        *resolve_api_key(config.provider).secrets,
        os.environ.get(config.ledger.database_url_env, ""),
    ):
        if secret:
            guard.register(secret)
    ledger = Ledger.from_config(git.root, config.ledger, clock=clock, guard=guard)
    try:
        project = ledger.add_project(git.root, git.origin_url())
    except (LedgerError, GitError):
        ledger.close()
        raise
    return ledger, Bugs(ledger, project.id)
