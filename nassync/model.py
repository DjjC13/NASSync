"""Data structures shared by the scan, plan, and execute stages.

Nothing here touches the filesystem or the GUI -- these types are the contract
between the layers, and are what gets serialised into the run journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum

from . import paths

#: Two SMB servers rarely agree on sub-second timestamps, and some filesystems
#: store mtime at 2s granularity. Anything inside this window counts as equal.
DEFAULT_MTIME_TOLERANCE = 2.0

#: Deleted items are moved here (under the target share root) instead of being
#: unlinked, unless the profile turns that off.
TRASH_DIR = ".nassync-trash"


class Action(str, Enum):
    """What NASSync intends to do about one path."""

    COPY = "copy"              # exists on source only
    OVERWRITE = "overwrite"    # exists on both, source wins
    DELETE = "delete"          # file exists on target only
    DELETE_DIR = "delete_dir"  # directory exists on target only
    MKDIR = "mkdir"            # directory exists on source only
    SKIP = "skip"              # already identical
    CONFLICT = "conflict"      # exists on both, target is newer
    UNSYNCABLE = "unsyncable"  # source name is illegal on Windows

    @property
    def is_destructive(self) -> bool:
        return self in (Action.DELETE, Action.DELETE_DIR)

    @property
    def writes_target(self) -> bool:
        return self in (Action.COPY, Action.OVERWRITE, Action.MKDIR)


class Resolution(str, Enum):
    """How the operator chose to settle a :attr:`Action.CONFLICT`."""

    UNRESOLVED = "unresolved"    # skipped, and reported as skipped
    OVERWRITE = "overwrite"      # source wins after all
    KEEP_TARGET = "keep_target"  # target wins, source copy discarded
    KEEP_BOTH = "keep_both"      # target renamed aside, then source copied in


class ItemState(str, Enum):
    """Execution outcome, tracked per item in the journal so runs can resume."""

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    ABANDONED = "abandoned"
    SKIPPED = "skipped"


@dataclass(slots=True)
class FileEntry:
    """One directory entry as seen during a scan."""

    relpath: str
    size: int
    mtime: float
    is_dir: bool

    @property
    def name(self) -> str:
        return self.relpath.rsplit("\\", 1)[-1]


@dataclass(slots=True)
class SharePair:
    """A source share mapped onto a target share."""

    source_server: str
    source_share: str
    target_server: str
    target_share: str
    enabled: bool = True

    @property
    def key(self) -> str:
        return f"{self.source_share}->{self.target_share}"

    @property
    def source_root(self) -> str:
        return self._root(self.source_server, self.source_share)

    @property
    def target_root(self) -> str:
        return self._root(self.target_server, self.target_share)

    @staticmethod
    def _root(server: str, share: str) -> str:
        """``\\\\server\\share``, or *share* verbatim when no server is given.

        The no-server form lets the CLI harness and the tests point a pair at
        ordinary local directories without pretending to be a file server.
        """
        if not server:
            return share
        return paths.share_root(server, share)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SharePair":
        return cls(**data)


@dataclass(slots=True)
class PlanItem:
    """A single intended change, and (later) what became of it."""

    pair_key: str
    relpath: str
    action: Action
    size: int = 0                    # bytes this item will move
    source_size: int | None = None
    source_mtime: float | None = None
    target_size: int | None = None
    target_mtime: float | None = None
    selected: bool = True            # unticked in the preview => not executed
    resolution: Resolution = Resolution.UNRESOLVED
    state: ItemState = ItemState.PENDING
    attempts: int = 0
    note: str = ""

    @property
    def is_actionable(self) -> bool:
        """True when this item would touch the target during execution."""
        if not self.selected:
            return False
        if self.action is Action.CONFLICT:
            return self.resolution in (Resolution.OVERWRITE, Resolution.KEEP_BOTH)
        return self.action not in (Action.SKIP, Action.UNSYNCABLE)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["action"] = self.action.value
        data["resolution"] = self.resolution.value
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PlanItem":
        data = dict(data)
        data["action"] = Action(data["action"])
        data["resolution"] = Resolution(data["resolution"])
        data["state"] = ItemState(data["state"])
        return cls(**data)


@dataclass
class ScanStats:
    """Running totals for one share pair's scan, for live GUI feedback."""

    source_files: int = 0
    source_dirs: int = 0
    source_bytes: int = 0
    target_files: int = 0
    target_dirs: int = 0
    target_bytes: int = 0
    excluded: int = 0
    #: Files already matching within tolerance. Counted, not listed as items.
    identical: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """The full set of intended changes across every enabled share pair."""

    items: list[PlanItem] = field(default_factory=list)
    stats: dict[str, ScanStats] = field(default_factory=dict)

    def by_action(self, action: Action) -> list[PlanItem]:
        return [i for i in self.items if i.action is action]

    def counts(self) -> dict[Action, int]:
        counts = {action: 0 for action in Action}
        for item in self.items:
            counts[item.action] += 1
        return counts

    def bytes_for(self, *actions: Action) -> int:
        wanted = set(actions)
        return sum(i.size for i in self.items if i.action in wanted and i.selected)

    @property
    def actionable(self) -> list[PlanItem]:
        return [i for i in self.items if i.is_actionable]

    @property
    def identical(self) -> int:
        """Files that already match, summed across share pairs."""
        return sum(s.identical for s in self.stats.values())

    @property
    def errors(self) -> list[str]:
        return [error for s in self.stats.values() for error in s.errors]
