"""Comparison-rule tests, run against real directory trees in a temp folder.

    python -m unittest discover -s tests

These use local directories rather than SMB shares -- the planner only ever
sees ScanResults, so the rules under test are identical either way.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from nassync.config import SyncOptions
from nassync.exclusions import DEFAULT_EXCLUSIONS, ExclusionSet
from nassync.model import Action, FileEntry, SharePair
from nassync.planner import build_plan, compare_trees
from nassync.scanner import scan_tree

NOW = time.time()


def write(root: Path, relpath: str, content: str = "x", mtime: float | None = None) -> Path:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class PlannerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "src"
        self.dst = self.root / "dst"
        self.src.mkdir()
        self.dst.mkdir()
        self.options = SyncOptions()
        self.exclusions = ExclusionSet(DEFAULT_EXCLUSIONS)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def plan_items(self):
        pair = SharePair("", str(self.src), "", str(self.dst))
        source = scan_tree(pair.source_root, self.exclusions)
        target = scan_tree(pair.target_root, self.exclusions)
        items, identical = compare_trees(pair, source, target, self.options)
        return {i.relpath: i for i in items}, identical

    def actions(self):
        items, _ = self.plan_items()
        return {relpath: item.action for relpath, item in items.items()}

    # --- the core matrix ---------------------------------------------------

    def test_source_only_file_is_copied(self):
        write(self.src, "new.txt")
        self.assertEqual(self.actions(), {"new.txt": Action.COPY})

    def test_identical_file_is_counted_not_listed(self):
        write(self.src, "same.txt", "hello", mtime=NOW)
        write(self.dst, "same.txt", "hello", mtime=NOW)
        items, identical = self.plan_items()
        self.assertEqual(items, {})
        self.assertEqual(identical, 1)

    def test_source_newer_is_overwritten(self):
        write(self.src, "edited.txt", "new content", mtime=NOW)
        write(self.dst, "edited.txt", "old", mtime=NOW - 3600)
        self.assertEqual(self.actions(), {"edited.txt": Action.OVERWRITE})

    def test_same_time_different_size_is_overwritten(self):
        write(self.src, "grown.txt", "much longer content", mtime=NOW)
        write(self.dst, "grown.txt", "short", mtime=NOW)
        self.assertEqual(self.actions(), {"grown.txt": Action.OVERWRITE})

    def test_target_newer_is_a_conflict(self):
        write(self.src, "doc.txt", "source", mtime=NOW - 3600)
        write(self.dst, "doc.txt", "edited on UNAS", mtime=NOW)
        self.assertEqual(self.actions(), {"doc.txt": Action.CONFLICT})

    def test_target_only_file_is_deleted(self):
        write(self.dst, "stale.txt")
        self.assertEqual(self.actions(), {"stale.txt": Action.DELETE})

    def test_timestamp_within_tolerance_counts_as_identical(self):
        write(self.src, "jitter.txt", "same", mtime=NOW)
        write(self.dst, "jitter.txt", "same", mtime=NOW + 1.5)
        _, identical = self.plan_items()
        self.assertEqual(identical, 1)

    def test_timestamp_beyond_tolerance_is_a_conflict(self):
        write(self.src, "jitter.txt", "same", mtime=NOW)
        write(self.dst, "jitter.txt", "same", mtime=NOW + 30)
        self.assertEqual(self.actions(), {"jitter.txt": Action.CONFLICT})

    # --- directories -------------------------------------------------------

    def test_target_only_directory_is_deleted_once_at_its_root(self):
        write(self.dst, "gone/a.txt")
        write(self.dst, "gone/deeper/b.txt")
        actions = self.actions()
        self.assertEqual(actions, {"gone": Action.DELETE_DIR})

    def test_delete_dir_reports_contained_file_count(self):
        write(self.dst, "gone/a.txt", "12345")
        write(self.dst, "gone/deeper/b.txt", "12345")
        items, _ = self.plan_items()
        item = items["gone"]
        self.assertEqual(item.size, 10)
        self.assertIn("2 file(s)", item.note)

    def test_empty_source_directory_is_created(self):
        (self.src / "empty").mkdir()
        self.assertEqual(self.actions(), {"empty": Action.MKDIR})

    def test_directory_implied_by_a_copy_is_not_listed_separately(self):
        write(self.src, "deep/nested/file.txt")
        self.assertEqual(self.actions(), {"deep\\nested\\file.txt": Action.COPY})

    def test_file_where_target_has_a_directory_is_a_conflict(self):
        write(self.src, "thing", "I am a file")
        (self.dst / "thing").mkdir()
        self.assertEqual(self.actions(), {"thing": Action.CONFLICT})

    # --- exclusions --------------------------------------------------------

    def test_excluded_names_are_never_copied_or_deleted(self):
        write(self.src, "Thumbs.db")
        write(self.src, "~$budget.xlsx")
        write(self.dst, "#recycle/old.txt")
        write(self.dst, ".nassync-trash/20260101/prior.txt")
        self.assertEqual(self.actions(), {})

    def test_excluded_directory_is_not_descended_into(self):
        write(self.src, "#recycle/deep/deeper/file.txt")
        result = scan_tree(str(self.src), self.exclusions)
        self.assertEqual(result.files, {})
        self.assertEqual(result.dirs, {})

    def test_qnap_recycle_bin_is_excluded_at_a_share_root(self):
        write(self.src, "@Recycle/deleted-by-a-user.txt")
        write(self.dst, "@Recycle/something-else.txt")
        self.assertEqual(self.actions(), {})

    def test_qnap_recycle_bin_is_excluded_at_any_depth(self):
        write(self.src, "Projects/Live/@Recycle/junk.txt")
        write(self.src, "Projects/Live/real.txt")
        actions = self.actions()
        self.assertNotIn("Projects\\Live\\@Recycle\\junk.txt", actions)
        self.assertEqual(actions, {"Projects\\Live\\real.txt": Action.COPY})

    def test_exclusion_matching_is_case_insensitive(self):
        write(self.src, "@RECYCLE/junk.txt")
        write(self.src, "thumbs.DB")
        self.assertEqual(self.actions(), {})

    # --- path-anchored exclusions ------------------------------------------

    def test_path_pattern_excludes_one_specific_folder_and_its_contents(self):
        self.exclusions = ExclusionSet([r"\Archive\2019"])
        write(self.src, "Archive/2019/old.txt")
        write(self.src, "Archive/2019/deeper/older.txt")
        write(self.src, "Archive/2020/keep.txt")
        copies = {p for p, a in self.actions().items() if a is Action.COPY}
        self.assertEqual(copies, {"Archive\\2020\\keep.txt"})

    def test_path_pattern_does_not_exclude_the_same_name_elsewhere(self):
        self.exclusions = ExclusionSet([r"\Archive\2019"])
        write(self.src, "Archive/2019/excluded.txt")
        write(self.src, "Projects/Archive/2019/kept.txt")
        copies = {p for p, a in self.actions().items() if a is Action.COPY}
        self.assertEqual(copies, {"Projects\\Archive\\2019\\kept.txt"})

    def test_leading_backslash_is_optional_on_path_patterns(self):
        self.exclusions = ExclusionSet([r"Archive\2019"])
        write(self.src, "Archive/2019/old.txt")
        copies = {p for p, a in self.actions().items() if a is Action.COPY}
        self.assertEqual(copies, set())

    def test_path_pattern_accepts_wildcards(self):
        self.exclusions = ExclusionSet([r"Projects\*\temp"])
        write(self.src, "Projects/Alpha/temp/scratch.txt")
        write(self.src, "Projects/Beta/temp/scratch.txt")
        write(self.src, "Projects/Alpha/final.txt")
        copies = {p for p, a in self.actions().items() if a is Action.COPY}
        self.assertEqual(copies, {"Projects\\Alpha\\final.txt"})

    def test_a_folder_holding_only_excluded_items_is_still_mirrored(self):
        # The folder itself is real on the source and not excluded, so the
        # target should have it -- just without the junk that was inside.
        write(self.src, "Live/@Recycle/junk.txt")
        self.assertEqual(self.actions(), {"Live": Action.MKDIR})

    def test_path_excluded_target_folder_is_never_deleted(self):
        # The whole point of symmetry: an excluded folder on the target must
        # survive a mirror, not get swept away as "not on the source".
        self.exclusions = ExclusionSet([r"\LocalOnly"])
        write(self.dst, "LocalOnly/important.txt")
        self.assertEqual(self.actions(), {})

    # --- unsyncable names --------------------------------------------------

    def test_windows_illegal_name_is_flagged_not_attempted(self):
        # Injected into the scan result rather than created on disk: Windows
        # itself refuses a trailing dot, but a Linux SMB server holds one
        # happily, which is exactly the case this rule exists for.
        source = scan_tree(str(self.src), self.exclusions)
        source.files["trailing."] = FileEntry("trailing.", size=1, mtime=NOW, is_dir=False)
        target = scan_tree(str(self.dst), self.exclusions)
        pair = SharePair("", str(self.src), "", str(self.dst))
        items, _ = compare_trees(pair, source, target, self.options)
        self.assertEqual([i.action for i in items], [Action.UNSYNCABLE])
        self.assertIn("not valid on Windows", items[0].note)

    # --- whole-plan behaviour ----------------------------------------------

    def test_build_plan_sorts_conflicts_before_deletions(self):
        write(self.src, "conflicted.txt", "a", mtime=NOW - 60)
        write(self.dst, "conflicted.txt", "b", mtime=NOW)
        write(self.dst, "stale.txt")
        pair = SharePair("", str(self.src), "", str(self.dst))
        plan = build_plan([pair], self.exclusions, self.options)
        self.assertEqual(
            [i.action for i in plan.items], [Action.CONFLICT, Action.DELETE]
        )

    def test_disabled_pair_is_skipped_entirely(self):
        write(self.src, "ignored.txt")
        pair = SharePair("", str(self.src), "", str(self.dst), enabled=False)
        plan = build_plan([pair], self.exclusions, self.options)
        self.assertEqual(plan.items, [])


if __name__ == "__main__":
    unittest.main()
