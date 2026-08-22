"""Crash-safe run journal, so an interrupted run can be picked up where it died.

Layout under ``%LOCALAPPDATA%\\NASSync\\runs\\<run_id>``::

    run.json       profile, options and share pairs -- written once at start
    plan.json      every plan item as approved by the operator -- written once
    progress.jsonl one line per item state change -- appended and flushed

Replaying ``progress.jsonl`` over ``plan.json`` reconstructs exactly where the
run got to. Append-only JSONL is used deliberately: a half-written final line is
discarded on load, whereas a half-rewritten state file would lose everything.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Profile, runs_dir
from .model import ItemState, Plan, PlanItem, Resolution


def new_run_id() -> str:
    """A sortable, filesystem-safe id: ``20260821-134502``."""
    return time.strftime("%Y%m%d-%H%M%S")


@dataclass
class RunSummary:
    """Enough about a past run to list it in a 'resume' dialog."""

    run_id: str
    profile_name: str
    started: str
    total_items: int
    done: int
    failed: int
    pending: int

    @property
    def is_complete(self) -> bool:
        return self.pending == 0


class RunJournal:
    """Records plan execution to disk as it happens."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.dir = runs_dir() / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._progress_file = None

    # --- creating -----------------------------------------------------------

    @classmethod
    def create(cls, profile: Profile, plan: Plan, run_id: str | None = None) -> "RunJournal":
        journal = cls(run_id or new_run_id())
        (journal.dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": journal.run_id,
                    "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "profile": profile.to_dict(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (journal.dir / "plan.json").write_text(
            json.dumps([item.to_dict() for item in plan.items], indent=2),
            encoding="utf-8",
        )
        return journal

    # --- recording ----------------------------------------------------------

    def _open(self):
        if self._progress_file is None:
            self._progress_file = open(
                self.dir / "progress.jsonl", "a", encoding="utf-8", buffering=1
            )
        return self._progress_file

    def record(self, item: PlanItem) -> None:
        """Persist one item's outcome. Flushed immediately -- a run that dies
        between items must still know what it had already finished."""
        handle = self._open()
        handle.write(
            json.dumps(
                {
                    "pair_key": item.pair_key,
                    "relpath": item.relpath,
                    "state": item.state.value,
                    "attempts": item.attempts,
                    "resolution": item.resolution.value,
                    "note": item.note,
                }
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())

    def close(self) -> None:
        if self._progress_file is not None:
            self._progress_file.close()
            self._progress_file = None

    def __enter__(self) -> "RunJournal":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # --- resuming -----------------------------------------------------------

    @classmethod
    def load(cls, run_id: str) -> tuple["RunJournal", Profile, Plan]:
        """Rebuild a journal, its profile, and its plan with states replayed."""
        journal = cls(run_id)
        run_data = json.loads((journal.dir / "run.json").read_text(encoding="utf-8"))
        profile = Profile.from_dict(run_data["profile"])

        plan = Plan()
        plan.items = [
            PlanItem.from_dict(d)
            for d in json.loads((journal.dir / "plan.json").read_text(encoding="utf-8"))
        ]

        by_key = {(i.pair_key, i.relpath.lower()): i for i in plan.items}
        progress_path = journal.dir / "progress.jsonl"
        if progress_path.exists():
            for line in progress_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    update = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn final line from a hard kill; ignore it
                item = by_key.get((update["pair_key"], update["relpath"].lower()))
                if item is None:
                    continue
                item.state = ItemState(update["state"])
                item.attempts = update.get("attempts", item.attempts)
                item.resolution = Resolution(update.get("resolution", item.resolution.value))
                item.note = update.get("note", item.note)
        return journal, profile, plan

    @staticmethod
    def list_runs() -> list[RunSummary]:
        """Summarise past runs, newest first, for the resume dialog."""
        summaries: list[RunSummary] = []
        for directory in sorted(runs_dir().glob("*"), reverse=True):
            if not (directory / "plan.json").exists():
                continue
            try:
                _, profile, plan = RunJournal.load(directory.name)
                run_data = json.loads(
                    (directory / "run.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError, KeyError):
                continue
            states = [i.state for i in plan.items if i.is_actionable]
            summaries.append(
                RunSummary(
                    run_id=directory.name,
                    profile_name=profile.name,
                    started=run_data.get("started", ""),
                    total_items=len(states),
                    done=sum(1 for s in states if s is ItemState.DONE),
                    failed=sum(1 for s in states if s is ItemState.FAILED),
                    pending=sum(1 for s in states if s is ItemState.PENDING),
                )
            )
        return summaries
