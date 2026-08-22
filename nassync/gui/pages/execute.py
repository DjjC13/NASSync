"""Step 4 -- the run itself: progress, throughput, and a live log."""

from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...executor import ExecProgress
from ...paths import human_bytes
from ..widgets import (
    Card,
    LogView,
    Muted,
    PageTitle,
    SectionTitle,
    TileRow,
    variant,
    verification_status,
)


class ExecutePage(QWidget):
    """Overall and per-file progress while the plan is applied."""

    pause_toggled = Signal(bool)
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._started = 0.0
        self._paused = False
        self._verifying = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self.title = PageTitle("Synchronizing")
        layout.addWidget(self.title)

        card = Card(spacing=8)
        self.phase_label = SectionTitle("Preparing…")
        card.add(self.phase_label)

        self.overall = QProgressBar()
        self.overall.setRange(0, 1000)
        self.overall.setTextVisible(False)
        card.add(self.overall)

        self.current_label = Muted("")
        card.add(self.current_label)

        self.file_progress = QProgressBar()
        self.file_progress.setRange(0, 100)
        self.file_progress.setTextVisible(False)
        self.file_progress.setProperty("variant", "thin")
        self.file_progress.setVisible(False)
        card.add(self.file_progress)
        layout.addWidget(card)

        self.tiles = TileRow(
            [
                ("items", "Items"),
                ("data", "Transferred"),
                ("rate", "Throughput"),
                ("elapsed", "Elapsed"),
            ]
        )
        layout.addWidget(self.tiles)

        self.log = LogView()
        layout.addWidget(self.log, 1)
        layout.addLayout(self._build_footer())

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self._toggle_pause)
        row.addWidget(self.pause_button)

        self.cancel_button = variant(QPushButton("Cancel"), "danger")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.cancel_button)
        return row

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_button.setText("Resume" if self._paused else "Pause")
        self.phase_label.setText("Paused" if self._paused else "Running")
        self.pause_toggled.emit(self._paused)

    def start(self) -> None:
        self._started = time.time()
        self._paused = False
        self._verifying = False
        self.title.setText("Synchronizing")
        self.pause_button.setText("Pause")
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Cancel")
        self.phase_label.setText("Preparing…")
        self.current_label.setText("")
        self.overall.setRange(0, 1000)  # a prior verification may have left it busy
        self.overall.setValue(0)
        self.log.clear()
        for key, value in (
            ("items", "0"), ("data", "0 B"), ("rate", "—"), ("elapsed", "0s")
        ):
            self.tiles.set(key, value)

    def begin_verification(self) -> None:
        """Switch the page into its verification state.

        The copy phase is over, so the bar becomes a busy indicator: the rescan
        has no meaningful percentage, but it must still be visibly alive.
        """
        self._verifying = True
        self.title.setText("Verifying")
        self.phase_label.setText("Verifying — re-reading both servers")
        self.current_label.setText("")
        self.file_progress.setVisible(False)
        self.overall.setRange(0, 0)
        self.pause_button.setEnabled(False)  # nothing left to pause
        self.cancel_button.setText("Stop verifying")

    def update_verification(self, event) -> None:
        """Report rescan progress, using the planner's PlanProgress events."""
        self.phase_label.setText(verification_status(event))
        if event.current:
            text = event.current
            self.current_label.setText(text if len(text) < 110 else "…" + text[-107:])

    def finish(self) -> None:
        self._verifying = False
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancel")
        self.file_progress.setVisible(False)
        self.overall.setRange(0, 1000)  # undo the busy indicator
        self.overall.setValue(self.overall.maximum())

    def append_log(self, line: str) -> None:
        self.log.append_line(line)

    def update_progress(self, event: ExecProgress) -> None:
        if self._verifying:
            return  # a late copy event must not clobber the verification state
        self.overall.setValue(int(event.fraction * 1000))
        if event.phase:
            self.phase_label.setText("Paused" if self._paused else event.phase)

        if event.current:
            text = event.current
            self.current_label.setText(text if len(text) < 110 else "…" + text[-107:])

        if event.file_percent is None:
            self.file_progress.setVisible(False)
        else:
            self.file_progress.setVisible(True)
            self.file_progress.setValue(int(event.file_percent))

        elapsed = time.time() - self._started
        self.tiles.set("items", f"{event.completed_items:,} / {event.total_items:,}")
        self.tiles.set("data", human_bytes(event.completed_bytes))
        # Below a second of samples the rate is meaningless -- dividing a few
        # megabytes by a few milliseconds reads as terabytes per second.
        self.tiles.set(
            "rate",
            f"{human_bytes(event.completed_bytes / elapsed)}/s" if elapsed >= 1.0 else "—",
        )
        self.tiles.set("elapsed", _duration(max(0.0, elapsed)))


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
