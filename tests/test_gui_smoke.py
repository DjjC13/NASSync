"""GUI smoke tests: construct every page and push data through it.

These do not open a window -- WA_DontShowOnScreen lets Qt lay widgets out and
render them without ever mapping them. They are skipped when PySide6 is not
installed, so the engine test suite still runs on a bare interpreter.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLineEdit
except ImportError:  # pragma: no cover - engine-only environment
    QApplication = None

from nassync.config import Profile, SyncOptions
from nassync.exclusions import DEFAULT_EXCLUSIONS, ExclusionSet
from nassync.executor import ExecProgress, RunResult
from nassync.model import Action, Resolution, SharePair
from nassync.planner import PlanProgress, build_plan
from nassync.verify import VerificationResult

NOW = time.time()


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class GuiSmokeTestCase(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.src = root / "src"
        self.dst = root / "dst"
        self.src.mkdir()
        self.dst.mkdir()

        # One of each interesting case, so the pages get real data to render.
        self._write(self.src, "new.txt", "new file", NOW)
        self._write(self.src, "edited.txt", "changed", NOW)
        self._write(self.dst, "edited.txt", "old", NOW - 9999)
        self._write(self.dst, "removed.txt", "gone from source", NOW - 9999)
        self._write(self.src, "conflicted.txt", "source", NOW - 9999)
        self._write(self.dst, "conflicted.txt", "target is newer", NOW)

        self.profile = Profile(
            name="smoke",
            source_server="OLDSERVER",
            target_server="NEWNAS",
            pairs=[SharePair("", str(self.src), "", str(self.dst))],
            exclusions=list(DEFAULT_EXCLUSIONS),
            options=SyncOptions(),
        )
        self.plan = build_plan(
            self.profile.pairs,
            ExclusionSet(self.profile.exclusions),
            self.profile.options,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _write(root: Path, relpath: str, content: str, mtime: float) -> None:
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def _window(self):
        from nassync.gui.main_window import MainWindow

        window = MainWindow()
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
        window.show()
        self.addCleanup(window.close)
        return window

    # --- tests --------------------------------------------------------------

    def test_window_builds_with_every_page(self):
        window = self._window()
        self.assertEqual(window.stack.count(), 6)

    def test_preview_reports_the_plan_and_enables_start(self):
        window = self._window()
        window.preview_page.set_plan(self.plan)
        self.assertEqual(window.preview_page.model.rowCount(), len(self.plan.items))
        self.assertTrue(window.preview_page.start_button.isEnabled())
        self.assertTrue(window.preview_page.conflicts_button.isEnabled())

    def test_unticking_a_row_in_the_view_deselects_the_plan_item(self):
        window = self._window()
        window.preview_page.set_plan(self.plan)
        model = window.preview_page.model
        model.setData(model.index(0, 0), Qt.Unchecked, Qt.CheckStateRole)
        self.assertFalse(model.item_at(0).selected)

    def test_action_filter_narrows_the_table(self):
        window = self._window()
        window.preview_page.set_plan(self.plan)
        proxy = window.preview_page.proxy
        proxy.set_action_filter({Action.DELETE})
        self.assertEqual(proxy.rowCount(), len(self.plan.by_action(Action.DELETE)))

    def test_conflict_decision_writes_back_to_the_plan_item(self):
        window = self._window()
        conflicts = self.plan.by_action(Action.CONFLICT)
        self.assertTrue(conflicts, "scenario should produce a conflict")
        window.conflicts_page.set_items(conflicts)
        window.conflicts_page._apply_bulk(Resolution.OVERWRITE)
        self.assertTrue(all(i.resolution is Resolution.OVERWRITE for i in conflicts))

    def test_share_mapping_pairs_matching_names_and_skips_the_rest(self):
        from nassync.shares import ShareInfo

        window = self._window()
        window.connect_page.source_edit.setText("OLDSERVER")
        window.connect_page.target_edit.setText("NEWNAS")
        window.connect_page.set_shares("source", [ShareInfo("Data"), ShareInfo("Extra")])
        window.connect_page.set_shares("target", [ShareInfo("Data")])

        pairs = window.connect_page.selected_pairs()
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].source_share, "Data")
        self.assertEqual(pairs[0].source_root, "\\\\OLDSERVER\\Data")

    # --- credentials --------------------------------------------------------

    def test_sign_in_fields_stay_hidden_until_a_connection_is_refused(self):
        window = self._window()
        self.assertFalse(window.connect_page.source_credentials.isVisible())
        self.assertFalse(window.connect_page.target_credentials.isVisible())

    def test_credential_failure_reveals_the_fields_for_that_server_only(self):
        window = self._window()
        window.connect_page.request_credentials(
            "source", "the user name or password is incorrect"
        )
        self.assertTrue(window.connect_page.source_credentials.isVisible())
        self.assertFalse(window.connect_page.target_credentials.isVisible())
        self.assertIn(
            "password", window.connect_page.source_credentials.message.text()
        )

    def test_password_field_is_masked(self):
        window = self._window()
        panel = window.connect_page.source_credentials
        self.assertEqual(panel.password.echoMode(), QLineEdit.Password)

    def test_submitting_credentials_reports_the_role(self):
        window = self._window()
        received = []
        window.connect_page.credentials_submitted.connect(
            lambda *args: received.append(args)
        )
        panel = window.connect_page.target_credentials
        panel.username.setText("CORP\\admin")
        panel.password.setText("hunter2")
        panel.sign_in_button.click()
        self.assertEqual(received, [("target", "CORP\\admin", "hunter2")])

    def test_submitting_without_a_username_does_not_emit(self):
        window = self._window()
        received = []
        window.connect_page.credentials_submitted.connect(
            lambda *args: received.append(args)
        )
        window.connect_page.source_credentials.sign_in_button.click()
        self.assertEqual(received, [])

    def test_successful_connection_hides_the_panel_and_clears_the_password(self):
        from nassync.shares import ShareInfo

        window = self._window()
        panel = window.connect_page.source_credentials
        window.connect_page.request_credentials("source", "denied")
        panel.username.setText("CORP\\admin")
        panel.password.setText("hunter2")

        window.connect_page.set_shares("source", [ShareInfo("Data")])

        self.assertFalse(panel.isVisible())
        self.assertEqual(panel.password.text(), "")

    def test_a_password_is_never_written_into_the_profile(self):
        window = self._window()
        panel = window.connect_page.source_credentials
        panel.username.setText("CORP\\admin")
        panel.password.setText("hunter2")

        collected = window.connect_page.collect_profile(Profile(name="p"))
        self.assertNotIn("hunter2", json.dumps(collected.to_dict()))

    def test_execute_page_survives_a_progress_update(self):
        window = self._window()
        window.execute_page.start()
        window.execute_page.update_progress(
            ExecProgress(
                completed_items=5, total_items=10,
                completed_bytes=500, total_bytes=1000,
                current="some\\file.txt", phase="Copying files",
            )
        )
        self.assertEqual(window.execute_page.overall.value(), 500)

    def test_verification_shows_a_live_indicator_rather_than_appearing_hung(self):
        window = self._window()
        window.execute_page.start()
        window.execute_page.begin_verification()

        # A zero range is Qt's busy indicator: the rescan has no percentage,
        # but it must still visibly be doing something.
        self.assertEqual(window.execute_page.overall.maximum(), 0)
        self.assertFalse(window.execute_page.pause_button.isEnabled())

        window.execute_page.update_verification(
            PlanProgress("Data->Data", "scan-source", files=48213, dirs=3120,
                         current="Accounts\\ledger.xlsx")
        )
        label = window.execute_page.phase_label.text()
        self.assertIn("re-reading the source", label)
        self.assertIn("48,213", label)

    def test_finishing_clears_the_busy_indicator(self):
        window = self._window()
        window.execute_page.start()
        window.execute_page.begin_verification()
        window.execute_page.finish()
        self.assertEqual(window.execute_page.overall.maximum(), 1000)
        self.assertEqual(
            window.execute_page.overall.value(), window.execute_page.overall.maximum()
        )

    def test_copy_progress_cannot_overwrite_the_verification_state(self):
        window = self._window()
        window.execute_page.start()
        window.execute_page.begin_verification()
        window.execute_page.update_progress(
            ExecProgress(completed_items=1, total_items=1, phase="Copying files")
        )
        self.assertIn("Verifying", window.execute_page.phase_label.text())

    def test_results_page_shows_verification_running_and_locks_the_button(self):
        window = self._window()
        window.summary_page.begin_verification()
        self.assertFalse(window.summary_page.verify_button.isEnabled())

        window.summary_page.update_verification(
            PlanProgress("Data->Data", "scan-target", files=101, dirs=7)
        )
        window.summary_page.set_verification(VerificationResult(identical=101))
        self.assertTrue(window.summary_page.verify_button.isEnabled())

    def test_summary_page_renders_a_result(self):
        window = self._window()
        result = RunResult(completed=8, skipped=1, bytes_copied=1234, deleted=2)
        verification = VerificationResult(identical=200)
        window.summary_page.set_result(
            self.profile, self.plan, result, verification, "run-1", None
        )
        self.assertIn("NASSync run summary", window.summary_page.summary_text.toPlainText())

    def test_profile_round_trips_through_the_connect_page(self):
        window = self._window()
        window.connect_page.apply_profile(self.profile)
        restored = window.connect_page.collect_profile(Profile(name="restored"))
        self.assertEqual(restored.source_server, "OLDSERVER")
        self.assertEqual(restored.exclusions, list(DEFAULT_EXCLUSIONS))
        self.assertTrue(restored.options.use_trash)


if __name__ == "__main__":
    unittest.main()
