"""Step 5 -- what happened, what failed, and the verification verdict."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...config import Profile
from ...executor import RunResult
from ...model import ItemState, Plan
from ...paths import human_bytes
from ...report import build_summary
from ...verify import VerificationResult
from ..models import FailureTableModel
from ..widgets import (
    Banner,
    LogView,
    PageTitle,
    TileRow,
    primary,
    variant,
    verification_status,
)


class SummaryPage(QWidget):
    """Outcome of the run, plus the tools to deal with anything that failed."""

    retry_requested = Signal()
    abandon_requested = Signal()
    verify_requested = Signal()
    open_reports_requested = Signal()
    new_run_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._report_dir: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(PageTitle("Results"))

        self.banner = Banner()
        layout.addWidget(self.banner)

        self.tiles = TileRow(
            [
                ("completed", "Completed"),
                ("copied", "Transferred"),
                ("removed", "Removed"),
                ("skipped", "Skipped"),
                ("failed", "Failed"),
            ]
        )
        layout.addWidget(self.tiles)

        self.tabs = QTabWidget()

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setLineWrapMode(QTextEdit.NoWrap)
        self.tabs.addTab(self.summary_text, "Summary")

        self.failures_model = FailureTableModel()
        self.failures_table = QTableView()
        self.failures_table.setModel(self.failures_model)
        self.failures_table.setAlternatingRowColors(True)
        self.failures_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.failures_table.verticalHeader().setVisible(False)
        self.failures_table.verticalHeader().setDefaultSectionSize(25)
        failure_header = self.failures_table.horizontalHeader()
        failure_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        failure_header.setSectionResizeMode(1, QHeaderView.Stretch)
        failure_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        failure_header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.tabs.addTab(self._wrap_failures(), "Failed items")

        self.log = LogView()
        self.tabs.addTab(self.log, "Log")
        layout.addWidget(self.tabs, 1)

        layout.addLayout(self._build_footer())

    def _wrap_failures(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.failures_table, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.retry_button = QPushButton("Retry")
        self.retry_button.setToolTip(
            "Attempt these items again — useful once whoever had them open has "
            "closed them."
        )
        self.retry_button.clicked.connect(self.retry_requested.emit)
        self.abandon_button = variant(QPushButton("Abandon"), "danger")
        self.abandon_button.setToolTip(
            "Give up on these items and record them in the report as known gaps."
        )
        self.abandon_button.clicked.connect(self.abandon_requested.emit)
        row.addWidget(self.retry_button)
        row.addWidget(self.abandon_button)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        new_run = QPushButton("New run")
        new_run.clicked.connect(self.new_run_requested.emit)
        row.addWidget(new_run)

        self.verify_button = QPushButton("Re-verify")
        self.verify_button.clicked.connect(self.verify_requested.emit)
        row.addWidget(self.verify_button)
        row.addStretch(1)

        self.open_reports = primary(QPushButton("Open reports"))
        self.open_reports.clicked.connect(self.open_reports_requested.emit)
        row.addWidget(self.open_reports)
        return row

    # --- population ---------------------------------------------------------

    def set_result(
        self,
        profile: Profile,
        plan: Plan,
        result: RunResult,
        verification: VerificationResult | None,
        run_id: str,
        report_dir: Path | None,
        log_text: str = "",
    ) -> None:
        self._report_dir = report_dir

        self.tiles.set("completed", f"{result.completed:,}")
        self.tiles.set("copied", human_bytes(result.bytes_copied))
        self.tiles.set("removed", f"{result.deleted:,}")
        self.tiles.set("skipped", f"{result.skipped:,}")
        self.tiles.set("failed", f"{len(result.failed):,}")
        self.tiles.emphasise("failed", "danger" if result.failed else None)

        self.summary_text.setPlainText(
            build_summary(profile, plan, result, verification, run_id)
        )
        if log_text:
            self.log.setPlainText(log_text)

        outstanding = [
            i for i in plan.items
            if i.state in (ItemState.FAILED, ItemState.ABANDONED)
        ]
        self.failures_model.set_items(outstanding)
        self.tabs.setTabText(1, f"Failed items ({len(outstanding)})")
        has_retryable = any(i.state is ItemState.FAILED for i in outstanding)
        self.retry_button.setEnabled(has_retryable)
        self.abandon_button.setEnabled(has_retryable)
        self.open_reports.setEnabled(report_dir is not None)

        self.banner.show_message(*_verdict(result, verification))
        self.tabs.setCurrentIndex(1 if outstanding else 0)

    def begin_verification(self) -> None:
        """Show that a standalone re-verification has started."""
        self.verify_button.setEnabled(False)
        self.banner.show_message("Verifying — re-reading both servers…", "accent")

    def update_verification(self, event) -> None:
        """Keep the banner alive while the rescan runs."""
        self.banner.show_message(verification_status(event), "accent")

    def set_verification(self, verification: VerificationResult) -> None:
        """Update just the verdict, after a standalone re-verification."""
        self.verify_button.setEnabled(True)
        self.banner.show_message(
            verification.headline, "success" if verification.passed else "danger"
        )


def _verdict(
    result: RunResult, verification: VerificationResult | None
) -> tuple[str, str]:
    """The one-line headline and its tone."""
    if result.cancelled:
        return (
            "Run cancelled. Completed work has been saved and the run can be "
            "resumed from the File menu.",
            "warning",
        )
    if result.failed:
        return (
            f"{len(result.failed)} item(s) could not be transferred. They are "
            "listed below — retry them once they are no longer in use.",
            "danger",
        )
    if verification is not None:
        return verification.headline, "success" if verification.passed else "danger"
    return "Run finished with no failures.", "success"
