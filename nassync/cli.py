"""Command-line harness for the NASSync engine.

Exists so the engine can be exercised, scripted, and debugged without the GUI::

    python -m nassync shares MNServer
    python -m nassync plan --source \\\\MNServer\\Data --target \\\\UNAS\\Data
    python -m nassync run   --source \\\\MNServer\\Data --target \\\\UNAS\\Data --yes
    python -m nassync verify --profile Migration
    python -m nassync gui

``--source``/``--target`` also accept plain local directories, which is how the
test suite drives it.
"""

from __future__ import annotations

import argparse
import sys
import threading

from . import paths
from .config import Profile, SyncOptions
from .exclusions import DEFAULT_EXCLUSIONS, ExclusionSet
from .executor import Executor
from .journal import RunJournal
from .model import Action, SharePair
from .planner import build_plan
from .report import build_summary, write_reports
from .shares import ShareEnumerationError, list_shares
from .verify import verify as run_verification


def _pair_from_paths(source: str, target: str) -> SharePair:
    """Build a share pair from two paths, UNC or local."""
    if source.startswith("\\\\"):
        source_server, source_share, rest = paths.split_unc(source)
        if rest:
            source_share = f"{source_share}\\{rest}"
    else:
        source_server, source_share = "", source
    if target.startswith("\\\\"):
        target_server, target_share, rest = paths.split_unc(target)
        if rest:
            target_share = f"{target_share}\\{rest}"
    else:
        target_server, target_share = "", target
    return SharePair(source_server, source_share, target_server, target_share)


def _profile_from_args(args) -> Profile:
    if getattr(args, "profile", None):
        matches = [p for p in Profile.list_saved() if p.stem.lower() == args.profile.lower()]
        if not matches:
            raise SystemExit(f"No saved profile named {args.profile!r}.")
        profile = Profile.load(matches[0])
    else:
        pair = _pair_from_paths(args.source, args.target)
        profile = Profile(
            name="cli",
            source_server=pair.source_server,
            target_server=pair.target_server,
            pairs=[pair],
            exclusions=list(DEFAULT_EXCLUSIONS),
            options=SyncOptions(),
        )
    if getattr(args, "no_trash", False):
        profile.options.use_trash = False
    return profile


def _build(profile: Profile, quiet: bool = False):
    def progress(event):
        if quiet or event.phase == "done":
            return
        sys.stderr.write(
            f"\r{event.phase}: {event.files} files, {event.dirs} folders  "
        )
        sys.stderr.flush()

    plan = build_plan(
        profile.enabled_pairs,
        ExclusionSet(profile.exclusions),
        profile.options,
        cancel=threading.Event(),
        progress=progress,
    )
    if not quiet:
        sys.stderr.write("\r" + " " * 70 + "\r")
    return plan


def _print_plan(plan, profile: Profile) -> None:
    counts = plan.counts()
    print(build_summary(profile, plan))
    if not plan.items:
        print("Nothing to do -- the target already matches the source.")
        return
    print("Planned changes")
    print("-" * 60)
    for item in plan.items[:500]:
        size = paths.human_bytes(item.size) if item.size else ""
        print(f"  {item.action.value:<11} {item.relpath}  {size}")
    if len(plan.items) > 500:
        print(f"  ... and {len(plan.items) - 500} more (see plan.csv)")
    if counts[Action.CONFLICT]:
        print(
            f"\n{counts[Action.CONFLICT]} conflict(s) will be SKIPPED -- "
            "resolve them in the GUI to act on them."
        )


def command_shares(args) -> int:
    try:
        found = list_shares(args.server, include_special=args.all)
    except ShareEnumerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not found:
        print(f"No disk shares visible on \\\\{args.server}.")
        return 0
    for share in found:
        remark = f"   {share.remark}" if share.remark else ""
        print(f"  {share.name}{remark}")
    return 0


def command_plan(args) -> int:
    profile = _profile_from_args(args)
    plan = _build(profile)
    _print_plan(plan, profile)
    if args.report:
        directory = write_reports(profile, plan, run_id="plan-only")
        print(f"\nPlan written to {directory}")
    return 0


def command_run(args) -> int:
    profile = _profile_from_args(args)
    plan = _build(profile)
    _print_plan(plan, profile)

    actionable = plan.actionable
    if not actionable:
        print("\nNothing to do.")
        return 0

    destructive = sum(1 for i in actionable if i.action.is_destructive)
    if not args.yes:
        if sys.stdin is None:
            print(
                "No console is attached, so the confirmation prompt cannot be "
                "shown. Re-run with --yes to proceed, or use the interface."
            )
            return 1
        print(
            f"\nAbout to change {len(actionable)} item(s), "
            f"including {destructive} deletion(s)."
        )
        if input("Type 'yes' to proceed: ").strip().lower() != "yes":
            print("Aborted.")
            return 1

    log_lines: list[str] = []
    with RunJournal.create(profile, plan) as journal:
        executor = Executor(
            profile,
            plan,
            journal=journal,
            on_log=lambda line: (log_lines.append(line), print(line))[0],
            on_progress=None,
        )
        result = executor.run()

    verification = None
    if profile.options.verify_after_run and not result.cancelled:
        print("\nVerifying...")
        verification = run_verification(profile)
        print(verification.headline)

    print()
    print(build_summary(profile, plan, result, verification, executor.run_id))
    directory = write_reports(
        profile, plan, result, verification, executor.run_id, "\n".join(log_lines)
    )
    print(f"Reports written to {directory}")
    return 0 if not result.failed and not result.cancelled else 1


def command_verify(args) -> int:
    profile = _profile_from_args(args)
    result = run_verification(profile)
    print(result.headline)
    for item in result.differences[:200]:
        print(f"  DIFFERS  [{item.action.value}] {item.relpath}")
    for item in result.accepted[:200]:
        print(f"  accepted [{item.action.value}] {item.relpath}")
    for error in result.errors[:50]:
        print(f"  error    {error}")
    return 0 if result.passed else 1


def command_runs(args) -> int:
    summaries = RunJournal.list_runs()
    if not summaries:
        print("No previous runs recorded.")
        return 0
    for summary in summaries:
        state = "complete" if summary.is_complete else f"{summary.pending} pending"
        print(
            f"  {summary.run_id}  {summary.profile_name:<20} {summary.started}  "
            f"{summary.done}/{summary.total_items} done, {summary.failed} failed  [{state}]"
        )
    return 0


def command_gui(args) -> int:
    from .gui.app import main as gui_main

    return gui_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nassync", description="Mirror one SMB share onto another."
    )
    sub = parser.add_subparsers(dest="command")

    def add_pair_arguments(target_parser):
        target_parser.add_argument("--source", help="source share or directory")
        target_parser.add_argument("--target", help="target share or directory")
        target_parser.add_argument("--profile", help="use a saved profile instead")
        target_parser.add_argument(
            "--no-trash",
            action="store_true",
            help="delete permanently instead of moving to .nassync-trash",
        )

    shares = sub.add_parser("shares", help="list the disk shares on a server")
    shares.add_argument("server")
    shares.add_argument("--all", action="store_true", help="include admin shares")
    shares.set_defaults(func=command_shares)

    plan = sub.add_parser("plan", help="scan and show what would change")
    add_pair_arguments(plan)
    plan.add_argument("--report", action="store_true", help="also write plan.csv")
    plan.set_defaults(func=command_plan)

    run = sub.add_parser("run", help="scan, then apply the changes")
    add_pair_arguments(run)
    run.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    run.set_defaults(func=command_run)

    verify_parser = sub.add_parser("verify", help="check the target mirrors the source")
    add_pair_arguments(verify_parser)
    verify_parser.set_defaults(func=command_verify)

    runs = sub.add_parser("runs", help="list previous runs")
    runs.set_defaults(func=command_runs)

    gui = sub.add_parser("gui", help="launch the graphical interface (default)")
    gui.set_defaults(func=command_gui)

    return parser


def _attach_streams() -> None:
    """Give a windowed build somewhere for its output to go.

    A PyInstaller ``--windowed`` executable has no console attached, so
    sys.stdout and sys.stderr are None and the very first print() would raise.
    Pointing them at a log file keeps the command line usable from the packaged
    executable, and captures any traceback that would otherwise vanish.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    from .config import app_dir

    handle = open(app_dir() / "console.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle


def main(argv: list[str] | None = None) -> int:
    _attach_streams()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        return command_gui(args)  # bare `python -m nassync` opens the GUI

    if args.command in ("plan", "run", "verify"):
        if not args.profile and not (args.source and args.target):
            parser.error("either --profile, or both --source and --target, is required")

    return args.func(args)
