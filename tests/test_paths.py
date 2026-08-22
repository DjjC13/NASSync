"""Path handling: extended-length prefixes, UNC splitting, and name legality."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nassync import paths
from nassync.exclusions import ExclusionSet
from nassync.scanner import scan_tree


class ExtendedPathTestCase(unittest.TestCase):
    r"""The \\?\ prefix disables path parsing, so separators must be normalised."""

    def test_local_path_gets_the_prefix(self):
        self.assertEqual(paths.extended(r"C:\x\y"), r"\\?\C:\x\y")

    def test_unc_path_gets_the_unc_prefix(self):
        self.assertEqual(paths.extended(r"\\srv\share\x"), r"\\?\UNC\srv\share\x")

    def test_forward_slashes_are_normalised(self):
        # Windows translates / to \ for ordinary paths but NOT behind \\?\,
        # so a path from a POSIX-speaking caller must be converted first.
        self.assertEqual(paths.extended("C:/x/y"), r"\\?\C:\x\y")

    def test_forward_slash_unc_is_normalised(self):
        self.assertEqual(paths.extended("//srv/share/x"), r"\\?\UNC\srv\share\x")

    def test_already_extended_passes_through_untouched(self):
        original = r"\\?\C:\x"
        self.assertEqual(paths.extended(original), original)

    def test_plain_reverses_extended(self):
        for original in (r"C:\x\y", r"\\srv\share\x"):
            self.assertEqual(paths.plain(paths.extended(original)), original)


class ScanWithForwardSlashesTestCase(unittest.TestCase):
    """The bug this guards against made every such scan silently return nothing."""

    def test_a_root_given_with_forward_slashes_still_scans(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sub"
            target.mkdir()
            (target / "file.txt").write_text("content", encoding="utf-8")

            result = scan_tree(str(Path(tmp)).replace("\\", "/"), ExclusionSet(()))
            self.assertEqual(result.errors, [])
            self.assertIn("sub\\file.txt", result.files)


class UncSplitTestCase(unittest.TestCase):
    def test_splits_server_share_and_remainder(self):
        self.assertEqual(
            paths.split_unc(r"\\MNServer\Data\Sub\Folder"),
            ("MNServer", "Data", r"Sub\Folder"),
        )

    def test_share_root_with_no_remainder(self):
        self.assertEqual(paths.split_unc(r"\\MNServer\Data"), ("MNServer", "Data", ""))

    def test_non_unc_is_rejected(self):
        with self.assertRaises(ValueError):
            paths.split_unc(r"C:\Data")


class IllegalNameTestCase(unittest.TestCase):
    """Linux SMB servers hold names Windows cannot create."""

    def test_trailing_dot_and_space_are_flagged(self):
        self.assertIsNotNone(paths.illegal_component("trailing."))
        self.assertIsNotNone(paths.illegal_component("trailing "))

    def test_reserved_device_names_are_flagged(self):
        for name in ("CON", "con.txt", "LPT1", "NUL"):
            self.assertIsNotNone(paths.illegal_component(name), name)

    def test_ordinary_names_pass(self):
        for name in ("report.docx", "Q-1041.pdf", "a.b.c", "CONTRACT.pdf"):
            self.assertIsNone(paths.illegal_component(name), name)

    def test_relpath_reports_the_offending_component(self):
        reason = paths.illegal_relpath(r"Projects\bad.\file.txt")
        self.assertIsNotNone(reason)
        self.assertIn("bad.", reason)


class HumanBytesTestCase(unittest.TestCase):
    def test_formats_each_magnitude(self):
        self.assertEqual(paths.human_bytes(0), "0 B")
        self.assertEqual(paths.human_bytes(512), "512 B")
        self.assertEqual(paths.human_bytes(1536), "1.5 KB")
        self.assertEqual(paths.human_bytes(5 * 1024**3), "5.0 GB")


if __name__ == "__main__":
    unittest.main()
