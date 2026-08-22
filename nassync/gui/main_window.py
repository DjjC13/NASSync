"""The NASSync main window: a linear flow from servers to verified mirror."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..config import Profile, profiles_dir, reports_dir
from ..executor import RunResult
from ..journal import RunJournal
from ..model import Action, ItemState, Plan
from ..report import write_plan_csv, write_reports
from ..verify import VerificationResult
from .pages import (
    ConflictsPage,
    ConnectPage,
    ExecutePage,
    PreviewPage,
    ScanPage,
    SummaryPage,
)
from .widgets import HeaderBar, StepRail
from .workers import ExecuteWorker, ScanWorker, ShareWorker, VerifyWorker

PAGE_CONNECT, PAGE_SCAN, PAGE_PREVIEW, PAGE_CONFLICTS, PAGE_EXECUTE, PAGE_SUMMARY = range(6)

#: Which rail step each page belongs to. Conflicts is a detour from Review.
_RAIL_POSITION = {
    PAGE_CONNECT: 0,
    PAGE_SCAN: 1,
    PAGE_PREVIEW: 2,
    PAGE_CONFLICTS: 2,
    PAGE_EXECUTE: 3,
    PAGE_SUMMARY: 4,
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NASSync")
        self.resize(1180, 760)

        self.profile = Profile(name="Untitled")
        self.plan: Plan | None = None
        self.journal: RunJournal | None = None
        self.result: RunResult | None = None
        self.verification: VerificationResult | None = None
        self.report_dir: Path | None = None
        self._log_lines: list[str] = []

        # Workers are kept on self so Python does not collect a running thread.
        self._share_workers: dict[str, ShareWorker] = {}
        self._scan_worker: ScanWorker | None = None
        self._execute_worker: ExecuteWorker | None = None
        self._verify_worker: VerifyWorker | None = None

        self._build_pages()
        self._build_menu()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready — enter the source and target server names.")

    # --- construction -------------------------------------------------------

    def _build_pages(self) -> None:
        self.stack = QStackedWidget()
        self.connect_page = ConnectPage()
        self.scan_page = ScanPage()
        self.preview_page = PreviewPage()
        self.conflicts_page = ConflictsPage()
        self.execute_page = ExecutePage()
        self.summary_page = SummaryPage()

        for page in (
            self.connect_page,
            self.scan_page,
            self.preview_page,
            self.conflicts_page,
            self.execute_page,
            self.summary_page,
        ):
            self.stack.addWidget(page)

        self.header = HeaderBar()
        self.rail = StepRail(
            ["Servers", "Analysis", "Review", "Execution", "Results"]
        )

        page_area = QWidget()
        page_area.setObjectName("PageArea")
        page_layout = QVBoxLayout(page_area)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(self.stack)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.rail)
        body.addWidget(page_area, 1)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.header)
        root_layout.addLayout(body, 1)
        self.setCentralWidget(root)
        self._go(PAGE_CONNECT)

        self.connect_page.connect_requested.connect(self._connect_server)
        self.connect_page.credentials_submitted.connect(self._submit_credentials)
        self.connect_page.scan_requested.connect(self._start_scan)

        self.scan_page.cancel_requested.connect(self._cancel_scan)

        self.preview_page.back_requested.connect(
            lambda: self._go(PAGE_CONNECT)
        )
        self.preview_page.conflicts_requested.connect(self._show_conflicts)
        self.preview_page.export_requested.connect(self._export_plan)
        self.preview_page.start_requested.connect(self._start_run)

        self.conflicts_page.done.connect(self._conflicts_done)

        self.execute_page.pause_toggled.connect(self._set_paused)
        self.execute_page.cancel_requested.connect(self._cancel_run)

        self.summary_page.retry_requested.connect(self._retry_failures)
        self.summary_page.abandon_requested.connect(self._abandon_failures)
        self.summary_page.verify_requested.connect(self._verify_again)
        self.summary_page.open_reports_requested.connect(self._open_reports)
        self.summary_page.new_run_requested.connect(self._new_run)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        save = QAction("&Save profile...", self)
        save.setShortcut("Ctrl+S")
        save.triggered.connect(self._save_profile)
        file_menu.addAction(save)

        load = QAction("&Open profile...", self)
        load.setShortcut("Ctrl+O")
        load.triggered.connect(self._load_profile)
        file_menu.addAction(load)

        file_menu.addSeparator()

        resume = QAction("&Resume an interrupted run...", self)
        resume.triggered.connect(self._resume_run)
        file_menu.addAction(resume)

        open_reports = QAction("Open re&ports folder", self)
        open_reports.triggered.connect(lambda: self._open_folder(reports_dir()))
        file_menu.addAction(open_reports)

        file_menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About NASSync", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _go(self, page: int) -> None:
        """Switch page and keep the step rail in step with it.

        Conflicts is a detour from Review rather than a stage of its own, so it
        shares Review's position on the rail.
        """
        self.stack.setCurrentIndex(page)
        self.rail.set_current(_RAIL_POSITION[page])
        self._refresh_header()

    def _refresh_header(self) -> None:
        source = self.profile.source_server
        target = self.profile.target_server
        if source and target:
            self.header.set_context(f"\\\\{source}  →  \\\\{target}")
        else:
            self.header.set_context("")

    # --- connecting ---------------------------------------------------------

    def _connect_server(self, role: str, server: str, username: str = "",
                        password: str = "") -> None:
        server = server.strip().strip("\\/")
        if not server:
            self.connect_page.set_status(role, "Enter a server name first.")
            return

        if username:
            self.connect_page.set_signing_in(role, True)
            self.connect_page.set_status(role, f"Signing in to \\\\{server}…")
        else:
            self.connect_page.set_status(role, f"Contacting \\\\{server}…")

        worker = ShareWorker(role, server, username, password, self)
        worker.listed.connect(self.connect_page.set_shares)
        worker.auth_required.connect(self._credentials_required)
        worker.failed.connect(lambda message, r=role: self._share_failed(r, message))
        self._share_workers[role] = worker
        worker.start()

    def _submit_credentials(self, role: str, username: str, password: str) -> None:
        """Retry the connection with the credentials the operator supplied."""
        edit = (
            self.connect_page.source_edit
            if role == "source"
            else self.connect_page.target_edit
        )
        self._connect_server(role, edit.text(), username, password)

    def _credentials_required(self, role: str, message: str) -> None:
        """A refusal a username and password could fix: reveal the fields."""
        self.connect_page.set_signing_in(role, False)
        self.connect_page.request_credentials(role, message)
        self.connect_page.banner.setVisible(False)
        self.statusBar().showMessage(
            f"\\\\{role.capitalize()} server requires credentials — "
            "sign in on the card above."
        )

    def _share_failed(self, role: str, message: str) -> None:
        self.connect_page.set_signing_in(role, False)
        self.connect_page.set_status(role, "Could not connect.")
        self.connect_page.banner.show_message(message.splitlines()[0], "danger")

    # --- scanning -----------------------------------------------------------

    def _start_scan(self) -> None:
        self.connect_page.collect_profile(self.profile)
        if not self.profile.enabled_pairs:
            self.connect_page.banner.show_message(
                "Tick at least one share and give it a target.", "warning"
            )
            return

        self.connect_page.banner.setVisible(False)
        self.scan_page.reset()
        self._go(PAGE_SCAN)
        self.statusBar().showMessage("Scanning both servers...")

        self._scan_worker = ScanWorker(self.profile, self)
        self._scan_worker.progressed.connect(self.scan_page.update_progress)
        self._scan_worker.finished_plan.connect(self._scan_finished)
        self._scan_worker.failed.connect(self._scan_failed)
        self._scan_worker.start()

    def _cancel_scan(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        self._go(PAGE_CONNECT)
        self.statusBar().showMessage("Scan cancelled.")

    def _scan_finished(self, plan: Plan) -> None:
        self.plan = plan
        self.preview_page.set_plan(plan)
        self._go(PAGE_PREVIEW)
        self.statusBar().showMessage(
            f"{len(plan.items):,} difference(s) found. Nothing has been changed."
        )

    def _scan_failed(self, message: str) -> None:
        self._go(PAGE_CONNECT)
        QMessageBox.critical(self, "Scan failed", message)

    # --- conflicts ----------------------------------------------------------

    def _show_conflicts(self) -> None:
        if self.plan is None:
            return
        self.conflicts_page.set_items(self.plan.by_action(Action.CONFLICT))
        self._go(PAGE_CONFLICTS)

    def _conflicts_done(self) -> None:
        self.preview_page.refresh()
        self._go(PAGE_PREVIEW)

    # --- running ------------------------------------------------------------

    def _start_run(self) -> None:
        if self.plan is None:
            return
        actionable = self.plan.actionable
        if not actionable:
            return
        if self.profile.options.confirm_before_execute and not self._confirm(actionable):
            return

        self._log_lines = []
        self.journal = RunJournal.create(self.profile, self.plan)

        # The approved plan is written before a single byte moves, so there is a
        # record of what was intended even if the run dies halfway.
        self.report_dir = reports_dir() / self.journal.run_id
        self.report_dir.mkdir(parents=True, exist_ok=True)
        write_plan_csv(self.report_dir / "plan.csv", self.plan)

        self.execute_page.start()
        self._go(PAGE_EXECUTE)
        self._launch_executor()

    def _launch_executor(self) -> None:
        assert self.plan is not None and self.journal is not None
        self._execute_worker = ExecuteWorker(self.profile, self.plan, self.journal, self)
        self._execute_worker.progressed.connect(self.execute_page.update_progress)
        self._execute_worker.logged.connect(self._log)
        self._execute_worker.verify_started.connect(self._verification_started)
        self._execute_worker.verify_progressed.connect(
            self.execute_page.update_verification
        )
        self._execute_worker.finished_run.connect(self._run_finished)
        self._execute_worker.failed.connect(self._run_failed)
        self._execute_worker.start()
        self.statusBar().showMessage("Sync in progress...")

    def _confirm(self, actionable: list) -> bool:
        deletions = sum(1 for i in actionable if i.action.is_destructive)
        overwrites = sum(1 for i in actionable if i.action is Action.OVERWRITE)
        where = "moved to .nassync-trash on the target" if self.profile.options.use_trash \
            else "PERMANENTLY DELETED"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if deletions else QMessageBox.Question)
        box.setWindowTitle("Confirm sync")
        box.setText(f"Apply {len(actionable):,} change(s) to the target?")
        box.setInformativeText(
            f"• {overwrites:,} file(s) will be updated\n"
            f"• {deletions:,} item(s) will be {where}\n\n"
            "The source server is not modified."
        )
        box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Yes)
        box.setDefaultButton(QMessageBox.Cancel)
        box.button(QMessageBox.Yes).setText("Execute sync")
        return box.exec() == QMessageBox.Yes

    def _verification_started(self) -> None:
        self.execute_page.begin_verification()
        self.statusBar().showMessage(
            "Verifying — re-reading both servers to confirm the mirror is complete."
        )

    def _set_paused(self, paused: bool) -> None:
        if self._execute_worker is not None:
            self._execute_worker.set_paused(paused)

    def _cancel_run(self) -> None:
        if self._execute_worker is None:
            return
        if (
            QMessageBox.question(
                self,
                "Cancel sync",
                "Stop the sync? Work already completed is kept, and the run can be "
                "resumed later from the File menu.",
                QMessageBox.No | QMessageBox.Yes,
                QMessageBox.No,
            )
            == QMessageBox.Yes
        ):
            self._execute_worker.cancel()

    def _log(self, line: str) -> None:
        self._log_lines.append(line)
        self.execute_page.append_log(line)

    def _run_finished(
        self, result: RunResult, verification: VerificationResult | None
    ) -> None:
        assert self.plan is not None
        self.result = result
        self.verification = verification
        self.execute_page.finish()

        if self.journal is not None:
            self.journal.close()
        run_id = self.journal.run_id if self.journal else ""
        self.report_dir = write_reports(
            self.profile,
            self.plan,
            result,
            verification,
            run_id,
            "\n".join(self._log_lines),
        )
        self.summary_page.set_result(
            self.profile,
            self.plan,
            result,
            verification,
            run_id,
            self.report_dir,
            "\n".join(self._log_lines),
        )
        self._go(PAGE_SUMMARY)
        self.statusBar().showMessage(
            f"Run finished. Reports written to {self.report_dir}"
        )

    def _run_failed(self, message: str) -> None:
        self.execute_page.finish()
        QMessageBox.critical(self, "Sync failed", message)

    # --- after the run ------------------------------------------------------

    def _retry_failures(self) -> None:
        if self.plan is None:
            return
        retryable = [i for i in self.plan.items if i.state is ItemState.FAILED]
        if not retryable:
            return
        for item in retryable:
            item.attempts = 0  # a manual retry starts the count again
        self.execute_page.start()
        self._go(PAGE_EXECUTE)
        self._launch_executor()

    def _abandon_failures(self) -> None:
        if self.plan is None:
            return
        for item in self.plan.items:
            if item.state is ItemState.FAILED:
                item.state = ItemState.ABANDONED
                if self.journal is not None:
                    self.journal.record(item)
        if self.result is not None:
            self.result.failed = []
            self.summary_page.set_result(
                self.profile,
                self.plan,
                self.result,
                self.verification,
                self.journal.run_id if self.journal else "",
                self.report_dir,
                "\n".join(self._log_lines),
            )

    def _verify_again(self) -> None:
        self.statusBar().showMessage("Verifying — re-reading both servers…")
        self.summary_page.begin_verification()
        self._verify_worker = VerifyWorker(self.profile, self)
        self._verify_worker.progressed.connect(self.summary_page.update_verification)
        self._verify_worker.finished_verify.connect(self._verification_done)
        self._verify_worker.failed.connect(self._verification_failed)
        self._verify_worker.start()

    def _verification_failed(self, message: str) -> None:
        self.summary_page.verify_button.setEnabled(True)
        self.statusBar().showMessage("Verification failed.")
        QMessageBox.critical(self, "Verification failed", message)

    def _verification_done(self, verification: VerificationResult) -> None:
        self.verification = verification
        self.summary_page.set_verification(verification)
        self.statusBar().showMessage(verification.headline)

    def _new_run(self) -> None:
        self.plan = None
        self.journal = None
        self.result = None
        self._go(PAGE_CONNECT)

    # --- profiles and reports ----------------------------------------------

    def _save_profile(self) -> None:
        self.connect_page.collect_profile(self.profile)
        name, accepted = QInputDialog.getText(
            self, "Save profile", "Profile name:", text=self.profile.name
        )
        if not accepted or not name.strip():
            return
        self.profile.name = name.strip()
        path = self.profile.save()
        self.statusBar().showMessage(f"Profile saved to {path}")

    def _load_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open profile", str(profiles_dir()), "NASSync profiles (*.json)"
        )
        if not path:
            return
        try:
            self.profile = Profile.load(path)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, "Could not open profile", str(exc))
            return
        self.connect_page.apply_profile(self.profile)
        self._go(PAGE_CONNECT)
        self.statusBar().showMessage(
            f"Loaded profile '{self.profile.name}'. Connect to refresh the share lists."
        )

    def _resume_run(self) -> None:
        summaries = [s for s in RunJournal.list_runs() if not s.is_complete]
        if not summaries:
            QMessageBox.information(
                self, "Resume a run", "There are no interrupted runs to resume."
            )
            return
        labels = [
            f"{s.run_id} -- {s.profile_name}: {s.done}/{s.total_items} done, "
            f"{s.pending} remaining"
            for s in summaries
        ]
        choice, accepted = QInputDialog.getItem(
            self, "Resume a run", "Pick a run to continue:", labels, 0, False
        )
        if not accepted:
            return

        run_id = summaries[labels.index(choice)].run_id
        try:
            self.journal, self.profile, self.plan = RunJournal.load(run_id)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, "Could not resume", str(exc))
            return

        self.connect_page.apply_profile(self.profile)
        self._log_lines = []
        self.execute_page.start()
        self._go(PAGE_EXECUTE)
        self._log(f"Resuming run {run_id}.")
        self._launch_executor()

    def _export_plan(self) -> None:
        if self.plan is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export plan", str(Path.home() / "nassync-plan.csv"), "CSV (*.csv)"
        )
        if not path:
            return
        write_plan_csv(Path(path), self.plan)
        self.statusBar().showMessage(f"Plan exported to {path}")

    def _open_reports(self) -> None:
        if self.report_dir is not None:
            self._open_folder(self.report_dir)

    @staticmethod
    def _open_folder(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))  # noqa: S606 - Windows-only application

    def _about(self) -> None:
        from .. import __version__

        QMessageBox.about(
            self,
            "About NASSync",
            f"<b>NASSync {__version__}</b><br><br>"
            "One-way SMB share mirroring for file server migrations.<br><br>"
            "The source is only ever read. The target is made to match it, with "
            "deletions moved to a dated trash folder rather than removed.<br><br>"
            "Copying is performed by Windows robocopy.",
        )

    # --- shutdown -----------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        running = self._execute_worker is not None and self._execute_worker.isRunning()
        if running:
            answer = QMessageBox.question(
                self,
                "Sync in progress",
                "A sync is still running. Stop it and close? The run can be resumed "
                "later.",
                QMessageBox.No | QMessageBox.Yes,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._execute_worker.cancel()
            self._execute_worker.wait(5000)

        for worker in (
            self._scan_worker,
            self._verify_worker,
            *self._share_workers.values(),
        ):
            if worker is not None and worker.isRunning():
                worker.cancel()
                worker.wait(2000)
        if self.journal is not None:
            self.journal.close()
        event.accept()
