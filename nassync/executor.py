"""Executes an approved plan: copies via robocopy, deletes via a trash folder.

Division of labour: NASSync decides *what* happens and records it, robocopy
moves the bytes. Robocopy is called per source directory with an explicit list
of filenames -- never with /E or /MIR -- so it can only ever touch files the
operator approved in the preview.

Files larger than :data:`LARGE_FILE_THRESHOLD` are copied one at a time so the
GUI can show intra-file progress; smaller ones are batched, because process
startup would otherwise dominate the transfer.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import paths
from .config import Profile, SyncOptions
from .journal import RunJournal, new_run_id
from .model import (
    TRASH_DIR,
    Action,
    ItemState,
    Plan,
    PlanItem,
    Resolution,
    SharePair,
)

#: Copy files at least this large individually, for per-file progress. Below
#: this, batching with /MT is worth far more than a per-file percentage.
LARGE_FILE_THRESHOLD = 256 * 1024 * 1024

#: Windows caps a command line at ~8191 chars; stay well clear of it.
MAX_COMMAND_LENGTH = 6000
#: Large batches are what let /MT keep its threads busy, so this is generous.
MAX_BATCH_FILES = 512

#: Robocopy sets bit 3 (8) when something could not be copied.
ROBOCOPY_FAILURE_BIT = 8

_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
_CREATE_NO_WINDOW = 0x08000000

#: Log verb per action, so the run log doubles as an audit trail.
_DONE_VERBS = {
    Action.COPY: "COPIED",
    Action.OVERWRITE: "UPDATED",
    Action.MKDIR: "CREATED",
    Action.DELETE: "REMOVED",
    Action.DELETE_DIR: "REMOVED",
    Action.CONFLICT: "COPIED",
    Action.SKIP: "SKIPPED",
    Action.UNSYNCABLE: "SKIPPED",
}


class ExecutionCancelled(Exception):
    """Raised internally when the operator cancels mid-run."""


@dataclass
class ExecProgress:
    """Snapshot of execution state, emitted continuously to the GUI."""

    completed_items: int = 0
    total_items: int = 0
    completed_bytes: int = 0
    total_bytes: int = 0
    current: str = ""
    file_percent: float | None = None
    phase: str = ""

    @property
    def fraction(self) -> float:
        if not self.total_bytes:
            return 1.0 if self.completed_items >= self.total_items else 0.0
        return min(1.0, self.completed_bytes / self.total_bytes)


@dataclass
class RunResult:
    """What a run achieved, for the report and the summary screen."""

    completed: int = 0
    skipped: int = 0
    bytes_copied: int = 0
    deleted: int = 0
    failed: list[PlanItem] = field(default_factory=list)
    cancelled: bool = False
    elapsed: float = 0.0


class Executor:
    """Runs a plan. Create one per run; call :meth:`run` from a worker thread."""

    def __init__(
        self,
        profile: Profile,
        plan: Plan,
        journal: RunJournal | None = None,
        on_progress=None,
        on_log=None,
        run_id: str | None = None,
    ):
        self.profile = profile
        self.plan = plan
        self.options: SyncOptions = profile.options
        self.run_id = run_id or (journal.run_id if journal else new_run_id())
        self.journal = journal
        self.on_progress = on_progress
        self.on_log = on_log

        self.cancel = threading.Event()
        self.paused = threading.Event()  # set == paused

        self._pairs = {p.key: p for p in profile.pairs}
        self._progress = ExecProgress()
        self._result = RunResult()
        #: Guards the progress counters, the result tallies, and journal writes,
        #: all of which are touched from several copy threads at once.
        self._lock = threading.Lock()

    # --- plumbing -----------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)

    def _emit(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self._progress, key, value)
        if self.on_progress is not None:
            self.on_progress(self._progress)

    def _checkpoint(self) -> None:
        """Honour pause and cancel between units of work."""
        while self.paused.is_set() and not self.cancel.is_set():
            time.sleep(0.1)
        if self.cancel.is_set():
            raise ExecutionCancelled()

    def _finish(self, item: PlanItem, state: ItemState, note: str = "") -> None:
        with self._lock:
            item.state = state
            if note:
                item.note = note
            if self.journal is not None:
                self.journal.record(item)

            if state is ItemState.DONE:
                self._result.completed += 1
                if item.action in (Action.DELETE, Action.DELETE_DIR):
                    self._result.deleted += 1
                else:
                    self._result.bytes_copied += item.size
            elif state is ItemState.SKIPPED:
                self._result.skipped += 1

            self._progress.completed_items += 1
            self._progress.completed_bytes += item.size
        self._emit(current=item.relpath, file_percent=None)

        if state is ItemState.DONE:
            self._log(f"{_DONE_VERBS[item.action]:<7} {item.relpath}")

    def _fail(self, item: PlanItem, note: str) -> None:
        """Park an item on the failed list, counting it as dealt with for now.

        Failures still advance the progress counters; :meth:`_retry_failures`
        winds them back if it gets another go at the item.
        """
        with self._lock:
            item.attempts += 1
            item.state = ItemState.FAILED
            item.note = note
            if self.journal is not None:
                self.journal.record(item)
            self._progress.completed_items += 1
            self._progress.completed_bytes += item.size
        self._emit(current=item.relpath, file_percent=None)
        self._log(f"FAILED  {item.relpath} -- {note}")

    def _pair(self, item: PlanItem) -> SharePair:
        return self._pairs[item.pair_key]

    def _source_path(self, item: PlanItem) -> str:
        return paths.join(self._pair(item).source_root, item.relpath)

    def _target_path(self, item: PlanItem) -> str:
        return paths.join(self._pair(item).target_root, item.relpath)

    # --- the run ------------------------------------------------------------

    def run(self) -> RunResult:
        """Execute every actionable, not-yet-done item in the plan."""
        started = time.time()
        pending = [
            item
            for item in self.plan.items
            if item.is_actionable and item.state in (ItemState.PENDING, ItemState.FAILED)
        ]
        for item in pending:
            item.state = ItemState.PENDING

        self._progress.total_items = len(pending)
        self._progress.total_bytes = sum(i.size for i in pending)
        self._emit(phase="starting")

        # Conflicts the operator left alone are recorded as deliberate skips.
        for item in self.plan.items:
            if item.action is Action.CONFLICT and not item.is_actionable:
                self._finish(item, ItemState.SKIPPED, item.note or "left for review")

        # Everything in `pending` is already actionable, so a conflict here has
        # been resolved to overwrite or keep-both and belongs with the copies.
        directories = [i for i in pending if i.action is Action.MKDIR]
        copies = [
            i for i in pending
            if i.action is not Action.MKDIR and not i.action.is_destructive
        ]
        deletions = [i for i in pending if i.action.is_destructive]

        try:
            self._make_directories(directories)
            self._copy_all(copies)
            self._delete_all(deletions)
            self._retry_failures()
        except ExecutionCancelled:
            self._result.cancelled = True
            self._log("Run cancelled. Progress has been saved and can be resumed.")

        self._result.failed = [i for i in self.plan.items if i.state is ItemState.FAILED]
        self._result.elapsed = time.time() - started
        self._emit(phase="finished", current="")
        return self._result

    # --- directories --------------------------------------------------------

    def _make_directories(self, items: list[PlanItem]) -> None:
        if not items:
            return
        self._emit(phase="Creating folders")
        for item in sorted(items, key=lambda i: i.relpath.count("\\")):
            self._checkpoint()
            path = paths.extended(self._target_path(item))
            try:
                os.makedirs(path, exist_ok=True)
                self._finish(item, ItemState.DONE)
            except OSError as exc:
                self._fail(item, f"could not create folder: {exc.strerror or exc}")

    # --- copying ------------------------------------------------------------

    def _copy_all(self, items: list[PlanItem]) -> None:
        copies = [i for i in items if i.is_actionable and i.action is not Action.MKDIR]
        if not copies:
            return
        self._emit(phase="Copying files")

        # "Keep both" renames the target's version aside before the copy lands.
        for item in copies:
            if item.action is Action.CONFLICT and item.resolution is Resolution.KEEP_BOTH:
                self._checkpoint()
                if not self._rename_aside(item):
                    continue

        remaining = [i for i in copies if i.state is ItemState.PENDING]
        by_directory: dict[tuple[str, str], list[PlanItem]] = {}
        for item in remaining:
            parent = item.relpath.rsplit("\\", 1)[0] if "\\" in item.relpath else ""
            by_directory.setdefault((item.pair_key, parent), []).append(item)

        # Large files go first and one at a time: there is nothing to overlap
        # within a single file, and a visible per-file percentage is worth more
        # than squeezing them alongside the batches.
        batch_tasks: list[tuple[str, str, list[PlanItem]]] = []
        for (pair_key, parent), group in by_directory.items():
            pair = self._pairs[pair_key]
            source_dir = paths.join(pair.source_root, parent)
            target_dir = paths.join(pair.target_root, parent)

            for item in (i for i in group if i.size >= LARGE_FILE_THRESHOLD):
                self._checkpoint()
                self._emit(current=item.relpath, file_percent=0.0)
                self._copy_batch(source_dir, target_dir, [item], track_percent=True)

            small = [i for i in group if i.size < LARGE_FILE_THRESHOLD]
            for batch in self._batches(small):
                batch_tasks.append((source_dir, target_dir, batch))

        self._run_batches(batch_tasks)

    def _run_batches(self, tasks: list[tuple[str, str, list[PlanItem]]]) -> None:
        """Copy batches, several directories at a time.

        Robocopy's /MT only parallelises inside one call, so a delta scattered
        across many small folders would otherwise run at one directory at a
        time -- which is the shape most file server migrations actually have.
        """
        if not tasks:
            return

        workers = max(1, min(self.options.parallel_directories, len(tasks)))
        if workers == 1:
            for source_dir, target_dir, batch in tasks:
                self._checkpoint()
                self._emit(current=batch[0].relpath, file_percent=None)
                self._copy_batch(source_dir, target_dir, batch, track_percent=False)
            return

        def work(task) -> None:
            source_dir, target_dir, batch = task
            self._checkpoint()
            self._emit(current=batch[0].relpath, file_percent=None)
            self._copy_batch(source_dir, target_dir, batch, track_percent=False)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, task) for task in tasks]
            cancelled = False
            for future in futures:
                try:
                    future.result()
                except ExecutionCancelled:
                    cancelled = True
                    for pending in futures:
                        pending.cancel()
            if cancelled:
                raise ExecutionCancelled()

    def _batches(self, items: list[PlanItem]):
        """Split items into robocopy invocations that fit on a command line."""
        batch: list[PlanItem] = []
        length = 0
        for item in items:
            name_length = len(item.relpath.rsplit("\\", 1)[-1]) + 3  # quotes + space
            if batch and (
                len(batch) >= MAX_BATCH_FILES or length + name_length > MAX_COMMAND_LENGTH
            ):
                yield batch
                batch, length = [], 0
            batch.append(item)
            length += name_length
        if batch:
            yield batch

    def _copy_batch(
        self,
        source_dir: str,
        target_dir: str,
        batch: list[PlanItem],
        track_percent: bool,
    ) -> None:
        names = [item.relpath.rsplit("\\", 1)[-1] for item in batch]
        code, output = self._robocopy(source_dir, target_dir, names, track_percent)

        # A cancelled robocopy exits non-zero; that is not a failure to report.
        if self.cancel.is_set():
            raise ExecutionCancelled()

        if code < ROBOCOPY_FAILURE_BIT:
            for item in batch:
                self._finish(item, ItemState.DONE)
            return

        # Something in this batch failed. Work out which, so one locked file
        # does not condemn the other fifty-nine.
        tail = (output.strip().splitlines() or [""])[-1].strip()[:160]
        for item in batch:
            if self._verify_copied(item):
                self._finish(item, ItemState.DONE)
            else:
                self._fail(item, f"robocopy exit {code}: {tail}")

    def _robocopy(
        self, source_dir: str, target_dir: str, names: list[str], track_percent: bool
    ) -> tuple[int, str]:
        """Invoke robocopy for specific filenames in one directory."""
        command = [
            "robocopy",
            source_dir.rstrip("\\"),
            target_dir.rstrip("\\"),
            *names,
            "/COPY:DAT",       # data, attributes, timestamps -- no ACLs (Linux SMB)
            "/DCOPY:DAT",
            # Robocopy gets a single quick retry for transient blips; the real
            # retry policy belongs to _retry_failures, which can report and be
            # cancelled between attempts.
            "/R:1", "/W:2",
            "/NJH", "/NJS",    # no job header or summary
            "/NDL", "/NC",     # no directory list or file class column
            "/BYTES",
        ]

        if self.options.restartable:
            # /Z journals every block so an interrupted file resumes rather than
            # restarting. It is also the single biggest throughput killer in
            # robocopy, which is why it is off by default.
            command.append("/Z")

        if track_percent:
            # One big file on its own: no threads to spread it across, but
            # unbuffered I/O avoids polluting the cache with data read once.
            if self.options.unbuffered_large_files:
                command.append("/J")
        else:
            # A batch of smaller files: throughput here is dominated by
            # per-file round trips, which is exactly what /MT overlaps.
            if self.options.copy_threads > 1:
                command.append(f"/MT:{self.options.copy_threads}")
            command.append("/NP")

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as exc:  # robocopy missing -- catastrophic, stop the run
            raise RuntimeError(f"Could not start robocopy: {exc}") from exc

        chunks: list[str] = []
        with process:
            assert process.stdout is not None
            for chunk in iter(lambda: process.stdout.read(256), ""):
                chunks.append(chunk)
                if track_percent:
                    found = _PERCENT.findall(chunk)
                    if found:
                        self._emit(file_percent=float(found[-1]))
                if self.cancel.is_set():
                    process.terminate()
                    break
            process.wait()
        return process.returncode, "".join(chunks)

    def _verify_copied(self, item: PlanItem) -> bool:
        """Confirm a copy landed, by comparing sizes on both sides."""
        try:
            source = os.stat(paths.extended(self._source_path(item)))
            target = os.stat(paths.extended(self._target_path(item)))
        except OSError:
            return False
        return source.st_size == target.st_size

    def _rename_aside(self, item: PlanItem) -> bool:
        """For 'keep both': move the target's version out of the way."""
        pair = self._pair(item)
        label = pair.target_server or "target"
        current = self._target_path(item)
        stem, dot, extension = item.relpath.rpartition(".")
        if dot and "\\" not in extension:
            renamed = f"{stem} ({label} copy).{extension}"
        else:
            renamed = f"{item.relpath} ({label} copy)"
        destination = paths.join(pair.target_root, renamed)
        try:
            os.replace(paths.extended(current), paths.extended(destination))
            self._log(f"KEPT    {item.relpath} -> {renamed.rsplit('\\', 1)[-1]}")
            return True
        except OSError as exc:
            self._fail(item, f"could not rename target copy: {exc.strerror or exc}")
            return False

    # --- deleting -----------------------------------------------------------

    def _delete_all(self, items: list[PlanItem]) -> None:
        if not items:
            return
        self._emit(phase="Removing files not on the source")
        # Deepest paths first, so a directory is emptied before it is removed.
        for item in sorted(items, key=lambda i: -i.relpath.count("\\")):
            self._checkpoint()
            self._emit(current=item.relpath)
            try:
                if self.options.use_trash:
                    self._move_to_trash(item)
                elif item.action is Action.DELETE_DIR:
                    shutil.rmtree(paths.extended(self._target_path(item)))
                else:
                    os.remove(paths.extended(self._target_path(item)))
                self._finish(item, ItemState.DONE)
            except FileNotFoundError:
                self._finish(item, ItemState.DONE, "already absent")
            except OSError as exc:
                self._fail(item, f"could not remove: {exc.strerror or exc}")

    def _move_to_trash(self, item: PlanItem) -> None:
        """Move a deletion into ``.nassync-trash\\<run id>`` on the target share.

        Same volume, so this is a rename rather than a copy -- effectively free,
        and it means a mistaken mirror is recoverable until the trash is emptied.
        """
        pair = self._pair(item)
        source = paths.extended(self._target_path(item))
        destination = paths.join(
            pair.target_root, f"{TRASH_DIR}\\{self.run_id}\\{item.relpath}"
        )
        parent = destination.rsplit("\\", 1)[0]
        os.makedirs(paths.extended(parent), exist_ok=True)

        final = destination
        suffix = 1
        while os.path.exists(paths.extended(final)):
            final = f"{destination} ({suffix})"
            suffix += 1
        try:
            os.replace(source, paths.extended(final))
        except OSError:
            # Falls back to a copy+delete if the rename is refused (for
            # instance across a junction or a filesystem boundary).
            shutil.move(source, paths.extended(final))

    # --- retries ------------------------------------------------------------

    def _retry_failures(self) -> None:
        """Give locked files another chance before parking them for the operator."""
        for attempt in range(1, self.options.retry_count + 1):
            failures = [
                i
                for i in self.plan.items
                if i.state is ItemState.FAILED and i.attempts < self.options.retry_count
            ]
            if not failures:
                return
            self._checkpoint()
            self._emit(phase=f"Retrying {len(failures)} item(s), attempt {attempt}")
            self._log(f"Retrying {len(failures)} item(s) in {self.options.retry_wait}s...")
            for _ in range(self.options.retry_wait * 10):
                self._checkpoint()
                time.sleep(0.1)

            for item in failures:
                self._checkpoint()
                # Retried items were already counted as completed once; undo
                # that so the progress bar does not run past 100%.
                self._progress.completed_items -= 1
                self._progress.completed_bytes -= item.size
                item.state = ItemState.PENDING
                if item.action.is_destructive:
                    self._delete_all([item])
                else:
                    pair = self._pair(item)
                    parent = item.relpath.rsplit("\\", 1)[0] if "\\" in item.relpath else ""
                    self._copy_batch(
                        paths.join(pair.source_root, parent),
                        paths.join(pair.target_root, parent),
                        [item],
                        track_percent=item.size >= LARGE_FILE_THRESHOLD,
                    )

    def abandon_failures(self) -> None:
        """Mark everything still failed as abandoned, and record why."""
        for item in self.plan.items:
            if item.state is ItemState.FAILED:
                item.state = ItemState.ABANDONED
                if self.journal is not None:
                    self.journal.record(item)
