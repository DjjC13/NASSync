"""Executor tests -- these invoke the real robocopy against temp directories.

Slower than the planner tests (each robocopy is a process launch) but they are
the only way to know that the copy, trash, and conflict paths actually work.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from nassync.config import Profile, SyncOptions
from nassync.exclusions import DEFAULT_EXCLUSIONS, ExclusionSet
from nassync.executor import Executor
from nassync.journal import RunJournal
from nassync.model import Action, ItemState, Resolution, SharePair, TRASH_DIR
from nassync.planner import build_plan

NOW = time.time()


def write(root: Path, relpath: str, content: str = "x", mtime: float | None = None) -> Path:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class ExecutorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "src"
        self.dst = self.root / "dst"
        self.src.mkdir()
        self.dst.mkdir()
        self.pair = SharePair("", str(self.src), "", str(self.dst))
        self.profile = Profile(
            name="test",
            pairs=[self.pair],
            exclusions=list(DEFAULT_EXCLUSIONS),
            options=SyncOptions(retry_count=1, retry_wait=0),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def execute(self, resolve=None):
        """Plan, optionally resolve conflicts, then run. Returns (plan, result)."""
        plan = build_plan(
            self.profile.pairs,
            ExclusionSet(self.profile.exclusions),
            self.profile.options,
        )
        if resolve is not None:
            for item in plan.items:
                if item.action is Action.CONFLICT:
                    item.resolution = resolve
        executor = Executor(self.profile, plan, run_id="testrun")
        return plan, executor.run()

    def trash(self, relpath: str) -> Path:
        return self.dst / TRASH_DIR / "testrun" / relpath

    # --- copying ------------------------------------------------------------

    def test_new_file_is_copied_with_contents(self):
        write(self.src, "reports/q3.txt", "quarterly numbers")
        _, result = self.execute()
        self.assertEqual(result.failed, [])
        self.assertEqual(
            (self.dst / "reports/q3.txt").read_text(encoding="utf-8"),
            "quarterly numbers",
        )

    def test_edited_file_is_overwritten(self):
        write(self.src, "notes.txt", "new version", mtime=NOW)
        write(self.dst, "notes.txt", "stale", mtime=NOW - 9999)
        _, result = self.execute()
        self.assertEqual(result.failed, [])
        self.assertEqual((self.dst / "notes.txt").read_text(encoding="utf-8"), "new version")

    def test_modified_time_is_preserved_across_the_copy(self):
        write(self.src, "dated.txt", "content", mtime=NOW - 86400)
        self.execute()
        self.assertAlmostEqual(
            (self.dst / "dated.txt").stat().st_mtime, NOW - 86400, delta=2
        )

    def test_empty_source_folder_is_created(self):
        (self.src / "empty").mkdir()
        _, result = self.execute()
        self.assertEqual(result.failed, [])
        self.assertTrue((self.dst / "empty").is_dir())

    # --- deleting -----------------------------------------------------------

    def test_target_only_file_goes_to_trash_not_oblivion(self):
        write(self.dst, "removed.txt", "still recoverable")
        _, result = self.execute()
        self.assertEqual(result.failed, [])
        self.assertFalse((self.dst / "removed.txt").exists())
        self.assertEqual(
            self.trash("removed.txt").read_text(encoding="utf-8"), "still recoverable"
        )

    def test_target_only_directory_is_moved_whole_to_trash(self):
        write(self.dst, "oldproject/a.txt", "a")
        write(self.dst, "oldproject/sub/b.txt", "b")
        _, result = self.execute()
        self.assertEqual(result.failed, [])
        self.assertFalse((self.dst / "oldproject").exists())
        self.assertEqual(self.trash("oldproject/sub/b.txt").read_text(encoding="utf-8"), "b")

    def test_trash_is_never_itself_mirrored_away(self):
        write(self.dst, f"{TRASH_DIR}/earlier/thing.txt", "from a previous run")
        _, result = self.execute()
        self.assertEqual(result.failed, [])
        self.assertTrue((self.dst / TRASH_DIR / "earlier" / "thing.txt").exists())

    def test_permanent_delete_when_trash_is_disabled(self):
        self.profile.options.use_trash = False
        write(self.dst, "removed.txt")
        write(self.dst, "gone_dir/inner.txt")
        _, result = self.execute()
        self.assertEqual(result.failed, [])
        self.assertFalse((self.dst / "removed.txt").exists())
        self.assertFalse((self.dst / "gone_dir").exists())
        self.assertFalse((self.dst / TRASH_DIR).exists())

    # --- conflicts ----------------------------------------------------------

    def _make_conflict(self):
        write(self.src, "shared.txt", "source version", mtime=NOW - 3600)
        write(self.dst, "shared.txt", "target version", mtime=NOW)

    def test_unresolved_conflict_leaves_both_sides_alone(self):
        self._make_conflict()
        plan, result = self.execute()
        self.assertEqual((self.dst / "shared.txt").read_text(encoding="utf-8"), "target version")
        self.assertEqual(result.skipped, 1)
        self.assertEqual(plan.items[0].state, ItemState.SKIPPED)

    def test_conflict_resolved_to_overwrite_takes_the_source(self):
        self._make_conflict()
        _, result = self.execute(resolve=Resolution.OVERWRITE)
        self.assertEqual(result.failed, [])
        self.assertEqual((self.dst / "shared.txt").read_text(encoding="utf-8"), "source version")

    def test_conflict_resolved_to_keep_target_changes_nothing(self):
        self._make_conflict()
        _, result = self.execute(resolve=Resolution.KEEP_TARGET)
        self.assertEqual((self.dst / "shared.txt").read_text(encoding="utf-8"), "target version")
        self.assertEqual(result.skipped, 1)

    def test_conflict_resolved_to_keep_both_preserves_each_version(self):
        self._make_conflict()
        _, result = self.execute(resolve=Resolution.KEEP_BOTH)
        self.assertEqual(result.failed, [])
        self.assertEqual((self.dst / "shared.txt").read_text(encoding="utf-8"), "source version")
        kept = list(self.dst.glob("shared (*copy).txt"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(encoding="utf-8"), "target version")

    # --- selection ----------------------------------------------------------

    def test_unticking_an_item_in_the_preview_prevents_it_running(self):
        write(self.src, "wanted.txt", "yes")
        write(self.src, "unwanted.txt", "no")
        plan = build_plan(
            self.profile.pairs, ExclusionSet(self.profile.exclusions), self.profile.options
        )
        for item in plan.items:
            if item.relpath == "unwanted.txt":
                item.selected = False
        Executor(self.profile, plan, run_id="testrun").run()
        self.assertTrue((self.dst / "wanted.txt").exists())
        self.assertFalse((self.dst / "unwanted.txt").exists())

    # --- parallel copying ---------------------------------------------------

    def test_many_directories_copy_correctly_in_parallel(self):
        # Exercises the directory-level thread pool: every file must arrive
        # exactly once, and the counters must not race.
        expected = {}
        for d in range(12):
            for f in range(8):
                rel = f"dir{d:02}/file{f}.txt"
                expected[rel] = f"content {d}-{f}"
                write(self.src, rel, expected[rel])

        self.profile.options.parallel_directories = 4
        _, result = self.execute()

        self.assertEqual(result.failed, [])
        self.assertEqual(result.completed, len(expected))
        for rel, content in expected.items():
            self.assertEqual((self.dst / rel).read_text(encoding="utf-8"), content)

    def test_parallel_and_sequential_paths_agree(self):
        for d in range(6):
            write(self.src, f"dir{d}/a.txt", f"value {d}")

        self.profile.options.parallel_directories = 1
        _, sequential = self.execute()
        shutil.rmtree(self.dst)
        self.dst.mkdir()

        self.profile.options.parallel_directories = 4
        _, parallel = self.execute()

        self.assertEqual(sequential.completed, parallel.completed)
        self.assertEqual(sequential.failed, parallel.failed)

    def test_restartable_mode_still_copies(self):
        # /Z is off by default now; make sure turning it back on is not broken.
        self.profile.options.restartable = True
        write(self.src, "file.txt", "restartable")
        _, result = self.execute()
        self.assertEqual(result.failed, [])
        self.assertEqual((self.dst / "file.txt").read_text(encoding="utf-8"), "restartable")

    def test_single_threaded_copy_still_works(self):
        self.profile.options.copy_threads = 1
        write(self.src, "file.txt", "single threaded")
        _, result = self.execute()
        self.assertEqual(result.failed, [])
        self.assertEqual((self.dst / "file.txt").read_text(encoding="utf-8"), "single threaded")

    # --- journal / resume ---------------------------------------------------

    def test_journal_records_completion_so_a_rerun_skips_finished_work(self):
        write(self.src, "one.txt", "1")
        write(self.src, "two.txt", "2")
        plan = build_plan(
            self.profile.pairs, ExclusionSet(self.profile.exclusions), self.profile.options
        )
        with RunJournal.create(self.profile, plan, run_id="journaltest") as journal:
            Executor(self.profile, plan, journal=journal, run_id="journaltest").run()

        _, _, reloaded = RunJournal.load("journaltest")
        self.assertTrue(all(i.state is ItemState.DONE for i in reloaded.items))

        # A second executor over the reloaded plan has nothing left to do.
        result = Executor(self.profile, reloaded, run_id="journaltest").run()
        self.assertEqual(result.completed, 0)


if __name__ == "__main__":
    unittest.main()
