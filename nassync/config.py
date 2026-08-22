"""Application directories, sync options, and saved profiles.

A *profile* is everything needed to repeat a run: the two servers, which shares
map to which, what to exclude, and the safety options. Profiles live as JSON in
``%LOCALAPPDATA%\\NASSync\\profiles`` so a second pass is a reopen-and-rescan.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .exclusions import DEFAULT_EXCLUSIONS
from .model import DEFAULT_MTIME_TOLERANCE, SharePair

APP_NAME = "NASSync"


def app_dir() -> Path:
    """Root of NASSync's per-user state, created on demand."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def profiles_dir() -> Path:
    path = app_dir() / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reports_dir() -> Path:
    path = app_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runs_dir() -> Path:
    """Where run journals live, so an interrupted run can be resumed."""
    path = app_dir() / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(name: str) -> str:
    """Reduce an arbitrary profile name to something safe to put on disk."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")
    return cleaned or "profile"


@dataclass
class SyncOptions:
    """Safety and behaviour switches for a run."""

    #: Timestamps within this many seconds count as identical.
    mtime_tolerance: float = DEFAULT_MTIME_TOLERANCE
    #: Move deletions to .nassync-trash instead of unlinking them.
    use_trash: bool = True
    #: Attempts per locked/failed file before it lands on the Failed list.
    retry_count: int = 3
    #: Seconds between attempts.
    retry_wait: int = 5

    # --- performance --------------------------------------------------------
    #: Robocopy /MT threads for batches of small files. Copying over SMB spends
    #: most of its time waiting on per-file round trips, so overlapping them is
    #: where nearly all of the speed comes from.
    copy_threads: int = 16
    #: Robocopy /J for large files: unbuffered I/O, which avoids filling the
    #: cache with data that is read exactly once.
    unbuffered_large_files: bool = True
    #: Robocopy /Z. Lets an interrupted file resume instead of restarting, at a
    #: severe throughput cost. Off unless the link is genuinely unreliable.
    restartable: bool = False
    #: How many directories to copy at once. /MT only parallelises within a
    #: single robocopy call, so a delta spread thinly over many folders needs
    #: this as well. Total concurrent streams is roughly this x copy_threads.
    parallel_directories: int = 3
    #: Run a full rescan afterwards and assert zero differences.
    verify_after_run: bool = True
    #: Require an explicit confirmation dialog before any destructive run.
    confirm_before_execute: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SyncOptions":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Profile:
    """A saved, repeatable sync configuration."""

    name: str = "Untitled"
    source_server: str = ""
    target_server: str = ""
    pairs: list[SharePair] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUSIONS))
    options: SyncOptions = field(default_factory=SyncOptions)

    @property
    def enabled_pairs(self) -> list[SharePair]:
        return [p for p in self.pairs if p.enabled]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_server": self.source_server,
            "target_server": self.target_server,
            "pairs": [p.to_dict() for p in self.pairs],
            "exclusions": list(self.exclusions),
            "options": self.options.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        return cls(
            name=data.get("name", "Untitled"),
            source_server=data.get("source_server", ""),
            target_server=data.get("target_server", ""),
            pairs=[SharePair.from_dict(p) for p in data.get("pairs", [])],
            exclusions=list(data.get("exclusions", DEFAULT_EXCLUSIONS)),
            options=SyncOptions.from_dict(data.get("options", {})),
        )

    def path(self) -> Path:
        return profiles_dir() / f"{safe_filename(self.name)}.json"

    def save(self) -> Path:
        target = self.path()
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Path | str) -> "Profile":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def list_saved(cls) -> list[Path]:
        return sorted(profiles_dir().glob("*.json"))
