"""Directory tree scanning over SMB.

Iterative (not recursive) so that deep trees cannot blow the stack, and every
path goes through :func:`paths.extended` so >260 character paths just work.
A scan never raises on a bad directory -- it records the error and keeps going,
because one unreadable folder should not cost you the whole run.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

from . import paths
from .exclusions import ExclusionSet
from .model import FileEntry


class ScanCancelled(Exception):
    """Raised when the caller's cancel event is set mid-scan."""


@dataclass
class ScanResult:
    """Everything one tree walk found.

    Keys in :attr:`files` and :attr:`dirs` are lower-cased relative paths.
    Windows treats paths case-insensitively, so that is the only key under which
    source and target can be meaningfully compared -- but a Linux SMB server can
    legitimately hold ``Report.doc`` and ``report.doc`` side by side, so those
    collisions are recorded in :attr:`case_collisions` rather than silently
    overwriting each other.
    """

    root: str
    files: dict[str, FileEntry] = field(default_factory=dict)
    dirs: dict[str, FileEntry] = field(default_factory=dict)
    excluded: int = 0
    errors: list[str] = field(default_factory=list)
    case_collisions: list[str] = field(default_factory=list)
    skipped_links: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files.values())

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ScanResult(root={self.root!r}, files={len(self.files)}, "
            f"dirs={len(self.dirs)}, errors={len(self.errors)})"
        )


def scan_tree(
    root: str,
    exclusions: ExclusionSet | None = None,
    cancel: threading.Event | None = None,
    progress=None,
    progress_every: int = 500,
) -> ScanResult:
    """Walk *root* and return every non-excluded file and directory beneath it.

    Args:
        root: UNC or local path to walk (plain form; extended internally).
        exclusions: Rules applied to each entry name as it is encountered, so an
            excluded directory is never descended into at all.
        cancel: Set this event to abort; raises :class:`ScanCancelled`.
        progress: Optional ``callable(files, dirs, total_bytes, current_relpath)``
            invoked roughly every *progress_every* entries.
        progress_every: How often to report progress, in entries.
    """
    exclusions = exclusions or ExclusionSet(())
    result = ScanResult(root=root)
    seen = 0
    total_bytes = 0  # running total; recomputing per tick would be quadratic

    # Stack of (relative path,) directories still to visit. "" is the root.
    stack: list[str] = [""]
    while stack:
        relpath = stack.pop()
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()

        abspath = paths.extended(paths.join(root, relpath))
        try:
            with os.scandir(abspath) as it:
                entries = list(it)
        except OSError as exc:
            result.errors.append(f"{paths.join(root, relpath) or root}: {exc.strerror or exc}")
            continue

        for entry in entries:
            name = entry.name
            child_rel = f"{relpath}\\{name}" if relpath else name
            if exclusions.excludes(child_rel, name):
                result.excluded += 1
                continue

            key = child_rel.lower()

            try:
                is_link = entry.is_symlink()
                is_dir = entry.is_dir(follow_symlinks=False)
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                result.errors.append(f"{paths.join(root, child_rel)}: {exc.strerror or exc}")
                continue

            if is_link:
                # Reparse points / symlinks are recorded and left alone: following
                # them risks copy loops and duplicated data.
                result.skipped_links.append(child_rel)
                continue

            record = FileEntry(
                relpath=child_rel,
                size=0 if is_dir else stat.st_size,
                mtime=stat.st_mtime,
                is_dir=is_dir,
            )

            bucket = result.dirs if is_dir else result.files
            existing = bucket.get(key)
            if existing is not None and existing.relpath != child_rel:
                result.case_collisions.append(child_rel)
                continue
            bucket[key] = record

            if is_dir:
                stack.append(child_rel)
            else:
                total_bytes += record.size

            seen += 1
            if progress is not None and seen % progress_every == 0:
                progress(len(result.files), len(result.dirs), total_bytes, child_rel)

    if progress is not None:
        progress(len(result.files), len(result.dirs), total_bytes, "")
    return result
