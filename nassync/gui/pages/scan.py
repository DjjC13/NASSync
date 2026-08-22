"""Step 2 -- reading both trees, with live counters."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...paths import human_bytes
from ...planner import PlanProgress
from ..widgets import Banner, Card, Muted, PageSubtitle, PageTitle, SectionTitle, TileRow

_PHASE_LABELS = {
    "scan-source": "Reading the source share",
    "scan-target": "Reading the target share",
    "compare": "Comparing the two trees",
    "done": "Preparing the review",
}


class ScanPage(QWidget):
    """Progress and running counts while the plan is built."""

    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(PageTitle("Analyzing"))
        layout.addWidget(
            PageSubtitle("Both servers are being read. Nothing is being modified.")
        )

        card = Card()
        self.phase_label = SectionTitle("Starting…")
        card.add(self.phase_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # scanning has no meaningful percentage
        self.progress.setTextVisible(False)
        card.add(self.progress)

        self.current_label = Muted("")
        card.add(self.current_label)
        layout.addWidget(card)

        self.tiles = TileRow(
            [
                ("source_files", "Source files"),
                ("source_bytes", "Source size"),
                ("target_files", "Target files"),
                ("target_bytes", "Target size"),
            ]
        )
        layout.addWidget(self.tiles)

        self.banner = Banner(
            "Large shares can take several minutes to read. The scan can be "
            "stopped at any point without consequence.",
            "info",
        )
        layout.addWidget(self.banner)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)

    def reset(self) -> None:
        for key in ("source_files", "target_files"):
            self.tiles.set(key, "0")
        for key in ("source_bytes", "target_bytes"):
            self.tiles.set(key, "0 B")
        self.phase_label.setText("Starting…")
        self.current_label.setText("")
        self.cancel_button.setEnabled(True)

    def update_progress(self, event: PlanProgress) -> None:
        label = _PHASE_LABELS.get(event.phase, event.phase)
        if event.pair_key:
            label = f"{label} — {event.pair_key}"
        self.phase_label.setText(label)

        if event.phase == "scan-source":
            self.tiles.set("source_files", f"{event.files:,}")
            self.tiles.set("source_bytes", human_bytes(event.total_bytes))
        elif event.phase == "scan-target":
            self.tiles.set("target_files", f"{event.files:,}")
            self.tiles.set("target_bytes", human_bytes(event.total_bytes))

        # Elided from the left: the useful part of a long path is the end.
        if event.current:
            text = event.current
            self.current_label.setText(text if len(text) < 110 else "…" + text[-107:])
