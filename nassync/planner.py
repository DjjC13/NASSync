"""Turn two scanned trees into a list of intended changes.

The comparison rules, in priority order, for each path:

===========================  ====================================
State                        Action
===========================  ====================================
source name illegal on Win   UNSYNCABLE (reported, never attempted)
type differs across sides    CONFLICT   (file vs directory)
source only                  COPY / MKDIR
target newer than source     CONFLICT   (operator decides)
size differs or source newer OVERWRITE
target only                  DELETE / DELETE_DIR
identical within tolerance   counted as identical, not listed
===========================  ====================================

Identical files are counted rather than materialised as plan items: a 1 TB
share can hold hundreds of thousands of them, and a list of "do nothing" rows
costs memory and tells the operator nothing.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import paths
from .config import SyncOptions
from .exclusions import ExclusionSet
from .model import Action, Plan, PlanItem, ScanStats, SharePair
from .scanner import ScanResult, scan_tree

#: Display order for the preview table -- destructive actions last.
ACTION_ORDER = {
    Action.CONFLICT: 0,
    Action.UNSYNCABLE: 1,
    Action.COPY: 2,
    Action.OVERWRITE: 3,
    Action.MKDIR: 4,
    Action.DELETE: 5,
    Action.DELETE_DIR: 6,
    Action.SKIP: 7,
}


@dataclass
class PlanProgress:
    """Live feedback while a plan is being built."""

    pair_key: str
    phase: str  # "scan-source" | "scan-target" | "compare" | "done"
    files: int = 0
    dirs: int = 0
    total_bytes: int = 0
    current: str = ""


def _ancestor_keys(key: str):
    """Yield the lower-cased keys of every parent directory of *key*."""
    parts = key.split("\\")
    for i in range(1, len(parts)):
        yield "\\".join(parts[:i])


def compare_trees(
    pair: SharePair,
    source: ScanResult,
    target: ScanResult,
    options: SyncOptions,
) -> tuple[list[PlanItem], int]:
    """Compare one scanned pair. Returns (plan items, identical file count)."""
    items: list[PlanItem] = []
    identical = 0
    tolerance = options.mtime_tolerance
    key_of = pair.key

    def add(action: Action, entry_relpath: str, **kwargs) -> PlanItem:
        item = PlanItem(pair_key=key_of, relpath=entry_relpath, action=action, **kwargs)
        items.append(item)
        return item

    # --- Directories that exist only on the target: delete recursively --------
    # Only the top-most such directory is listed; everything beneath it goes
    # with it, so listing the children too would just be noise in the preview.
    target_only_dirs = {
        key for key in target.dirs if key not in source.dirs and key not in source.files
    }
    doomed_dir_roots = [
        key
        for key in target_only_dirs
        if not any(parent in target_only_dirs for parent in _ancestor_keys(key))
    ]

    def under_doomed_dir(key: str) -> bool:
        return any(parent in target_only_dirs for parent in _ancestor_keys(key))

    for key in sorted(doomed_dir_roots):
        entry = target.dirs[key]
        contained = [f for k, f in target.files.items() if k.startswith(key + "\\")]
        add(
            Action.DELETE_DIR,
            entry.relpath,
            size=sum(f.size for f in contained),
            target_size=None,
            target_mtime=entry.mtime,
            note=f"{len(contained)} file(s) beneath it",
        )

    # --- Source files ---------------------------------------------------------
    copy_keys: set[str] = set()
    for key, src in source.files.items():
        illegal = paths.illegal_relpath(src.relpath)
        if illegal:
            add(Action.UNSYNCABLE, src.relpath, size=src.size,
                source_size=src.size, source_mtime=src.mtime,
                note=f"name is not valid on Windows -- {illegal}")
            continue

        if key in target.dirs:
            add(Action.CONFLICT, src.relpath, size=src.size,
                source_size=src.size, source_mtime=src.mtime,
                target_mtime=target.dirs[key].mtime,
                note="a directory exists at this path on the target")
            continue

        dst = target.files.get(key)
        if dst is None:
            add(Action.COPY, src.relpath, size=src.size,
                source_size=src.size, source_mtime=src.mtime)
            copy_keys.add(key)
            continue

        if dst.mtime > src.mtime + tolerance:
            add(Action.CONFLICT, src.relpath, size=src.size,
                source_size=src.size, source_mtime=src.mtime,
                target_size=dst.size, target_mtime=dst.mtime,
                note="target copy is newer than the source")
            continue

        if dst.size != src.size or src.mtime > dst.mtime + tolerance:
            reason = "size differs" if dst.size != src.size else "source is newer"
            add(Action.OVERWRITE, src.relpath, size=src.size,
                source_size=src.size, source_mtime=src.mtime,
                target_size=dst.size, target_mtime=dst.mtime, note=reason)
            copy_keys.add(key)
            continue

        identical += 1

    # --- Source directories: create the ones no copy would create anyway ------
    implied_by_copies: set[str] = set()
    for key in copy_keys:
        implied_by_copies.update(_ancestor_keys(key))

    mkdir_candidates: list[str] = []
    for key, src_dir in source.dirs.items():
        if key in target.dirs or key in implied_by_copies:
            continue
        if key in target.files:
            add(Action.CONFLICT, src_dir.relpath,
                note="a file exists at this path on the target")
            continue
        illegal = paths.illegal_relpath(src_dir.relpath)
        if illegal:
            add(Action.UNSYNCABLE, src_dir.relpath,
                note=f"folder name is not valid on Windows -- {illegal}")
            continue
        mkdir_candidates.append(key)

    # Creating the deepest folder creates its parents too, so listing the
    # parents as well would just pad the preview with redundant rows.
    redundant = {
        parent for key in mkdir_candidates for parent in _ancestor_keys(key)
    }
    for key in mkdir_candidates:
        if key not in redundant:
            add(Action.MKDIR, source.dirs[key].relpath, note="empty folder on source")

    # --- Target files with no source counterpart: delete ----------------------
    for key, dst in target.files.items():
        if key in source.files or key in source.dirs:
            continue
        if under_doomed_dir(key):
            continue  # its parent directory is already being removed
        add(Action.DELETE, dst.relpath, size=dst.size,
            target_size=dst.size, target_mtime=dst.mtime)

    return items, identical


def build_plan(
    pairs: list[SharePair],
    exclusions: ExclusionSet,
    options: SyncOptions,
    cancel: threading.Event | None = None,
    progress=None,
) -> Plan:
    """Scan and compare every enabled share pair, producing a full :class:`Plan`.

    Source and target are scanned concurrently -- they are different servers, so
    there is no reason to wait for one before starting the other.
    """
    plan = Plan()

    def report(event: PlanProgress) -> None:
        if progress is not None:
            progress(event)

    for pair in pairs:
        if not pair.enabled:
            continue
        if cancel is not None and cancel.is_set():
            break

        stats = ScanStats()
        plan.stats[pair.key] = stats

        def make_progress(phase: str):
            def _cb(files: int, dirs: int, total: int, current: str) -> None:
                report(PlanProgress(pair.key, phase, files, dirs, total, current))
            return _cb

        with ThreadPoolExecutor(max_workers=2) as pool:
            source_future = pool.submit(
                scan_tree, pair.source_root, exclusions, cancel, make_progress("scan-source")
            )
            target_future = pool.submit(
                scan_tree, pair.target_root, exclusions, cancel, make_progress("scan-target")
            )
            source = source_future.result()
            target = target_future.result()

        stats.source_files = len(source.files)
        stats.source_dirs = len(source.dirs)
        stats.source_bytes = source.total_bytes
        stats.target_files = len(target.files)
        stats.target_dirs = len(target.dirs)
        stats.target_bytes = target.total_bytes
        stats.excluded = source.excluded + target.excluded
        stats.errors = source.errors + target.errors

        for relpath in source.case_collisions:
            stats.errors.append(
                f"{paths.join(pair.source_root, relpath)}: another file differs only "
                "by capitalisation and cannot be represented on Windows"
            )
        for relpath in source.skipped_links:
            stats.errors.append(
                f"{paths.join(pair.source_root, relpath)}: symbolic link skipped"
            )

        report(PlanProgress(pair.key, "compare"))
        items, identical = compare_trees(pair, source, target, options)
        stats.identical = identical
        plan.items.extend(items)

    plan.items.sort(key=lambda i: (i.pair_key, ACTION_ORDER[i.action], i.relpath.lower()))
    report(PlanProgress("", "done"))
    return plan
