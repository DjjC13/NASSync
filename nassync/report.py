"""Run reports: the paper trail for a migration cutover.

Four files per run, under ``%LOCALAPPDATA%\\NASSync\\reports\\<run id>``:

===============  ===========================================================
plan.csv         every intended change, as approved -- written before any writes
results.csv      the same items with their outcome -- written after the run
summary.txt      human-readable totals, failures, and verification verdict
nassync.log      the running log exactly as shown on screen
===============  ===========================================================
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from . import paths
from .config import Profile, reports_dir
from .executor import RunResult
from .model import Action, ItemState, Plan, PlanItem
from .verify import VerificationResult

_PLAN_COLUMNS = [
    "share_pair", "action", "relative_path", "bytes",
    "source_size", "source_modified", "target_size", "target_modified", "note",
]
_RESULT_COLUMNS = _PLAN_COLUMNS + ["selected", "resolution", "state", "attempts"]


def _timestamp(value: float | None) -> str:
    if not value:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def _plan_row(item: PlanItem) -> list:
    return [
        item.pair_key,
        item.action.value,
        item.relpath,
        item.size,
        "" if item.source_size is None else item.source_size,
        _timestamp(item.source_mtime),
        "" if item.target_size is None else item.target_size,
        _timestamp(item.target_mtime),
        item.note,
    ]


def write_plan_csv(path: Path, plan: Plan) -> Path:
    """Write the approved plan. Call this *before* execution begins."""
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(_PLAN_COLUMNS)
        for item in plan.items:
            writer.writerow(_plan_row(item))
    return path


def write_results_csv(path: Path, plan: Plan) -> Path:
    """Write every item with what actually became of it."""
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(_RESULT_COLUMNS)
        for item in plan.items:
            writer.writerow(
                _plan_row(item)
                + [item.selected, item.resolution.value, item.state.value, item.attempts]
            )
    return path


def build_summary(
    profile: Profile,
    plan: Plan,
    result: RunResult | None = None,
    verification: VerificationResult | None = None,
    run_id: str = "",
) -> str:
    """Compose the human-readable summary shown on screen and saved to disk."""
    lines: list[str] = []
    add = lines.append

    add("NASSync run summary")
    add("=" * 60)
    add(f"Run id       : {run_id}")
    add(f"Profile      : {profile.name}")
    add(f"Finished     : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if profile.source_server:
        add(f"Source       : \\\\{profile.source_server}")
    if profile.target_server:
        add(f"Target       : \\\\{profile.target_server}")
    add("")

    add("Share pairs")
    add("-" * 60)
    for pair in profile.enabled_pairs:
        stats = plan.stats.get(pair.key)
        add(f"  {pair.source_root}  ->  {pair.target_root}")
        if stats:
            add(
                f"      source: {stats.source_files} files, "
                f"{paths.human_bytes(stats.source_bytes)}   "
                f"target: {stats.target_files} files, "
                f"{paths.human_bytes(stats.target_bytes)}"
            )
    add("")

    counts = plan.counts()
    add("Planned")
    add("-" * 60)
    add(f"  Already identical : {plan.identical}")
    add(f"  To copy           : {counts[Action.COPY]}")
    add(f"  To overwrite      : {counts[Action.OVERWRITE]}")
    add(f"  Folders to create : {counts[Action.MKDIR]}")
    add(f"  To delete         : {counts[Action.DELETE]} file(s), "
        f"{counts[Action.DELETE_DIR]} folder(s)")
    add(f"  Conflicts         : {counts[Action.CONFLICT]}")
    add(f"  Unsyncable names  : {counts[Action.UNSYNCABLE]}")
    add("")

    if result is not None:
        add("Result")
        add("-" * 60)
        add(f"  Completed    : {result.completed}")
        add(f"  Data copied  : {paths.human_bytes(result.bytes_copied)}")
        add(f"  Removed      : {result.deleted}")
        add(f"  Skipped      : {result.skipped}")
        add(f"  Failed       : {len(result.failed)}")
        add(f"  Elapsed      : {result.elapsed:.1f}s")
        if result.cancelled:
            add("  NOTE: run was cancelled before finishing.")
        add("")

        abandoned = [i for i in plan.items if i.state is ItemState.ABANDONED]
        if result.failed or abandoned:
            add("Items needing attention")
            add("-" * 60)
            for item in list(result.failed) + abandoned:
                add(f"  [{item.state.value}] {item.pair_key} {item.relpath}")
                add(f"      {item.note}")
            add("")

    unsyncable = plan.by_action(Action.UNSYNCABLE)
    if unsyncable:
        add("Names that cannot exist on Windows (not copied)")
        add("-" * 60)
        for item in unsyncable:
            add(f"  {item.relpath}")
            add(f"      {item.note}")
        add("")

    errors = plan.errors
    if errors:
        add("Scan errors")
        add("-" * 60)
        for error in errors:
            add(f"  {error}")
        add("")

    if verification is not None:
        add("Verification")
        add("-" * 60)
        add(f"  {verification.headline}")
        add(f"  Files matching : {verification.identical}")
        for item in verification.differences[:200]:
            add(f"  DIFFERS  [{item.action.value}] {item.relpath}")
        if len(verification.differences) > 200:
            add(f"  ... and {len(verification.differences) - 200} more")
        add("")

    return "\n".join(lines)


def write_reports(
    profile: Profile,
    plan: Plan,
    result: RunResult | None = None,
    verification: VerificationResult | None = None,
    run_id: str = "",
    log_text: str = "",
    directory: Path | None = None,
) -> Path:
    """Write the full report set and return the directory containing it."""
    target = Path(directory) if directory else reports_dir() / (run_id or "unsaved")
    target.mkdir(parents=True, exist_ok=True)

    write_plan_csv(target / "plan.csv", plan)
    if result is not None:
        write_results_csv(target / "results.csv", plan)
    (target / "summary.txt").write_text(
        build_summary(profile, plan, result, verification, run_id), encoding="utf-8"
    )
    if log_text:
        (target / "nassync.log").write_text(log_text, encoding="utf-8")
    return target
