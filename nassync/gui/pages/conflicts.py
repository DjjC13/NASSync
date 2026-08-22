"""Step 3b -- files that are newer on the target than on the source.

These are the only cases where NASSync refuses to guess: a newer file on the
target usually means somebody has already started working on the new server,
and replacing it would destroy real work. The default is therefore to skip.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...model import PlanItem, Resolution
from ..models import ConflictTableModel, ResolutionDelegate
from ..widgets import Banner, Muted, PageSubtitle, PageTitle, primary


class ConflictsPage(QWidget):
    """A decision per conflicted file, with bulk application."""

    done = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(PageTitle("Conflicts"))
        layout.addWidget(
            PageSubtitle(
                "These files exist on both servers, but the target's copy has "
                "been modified more recently — most likely edited directly on "
                "the new server. Anything left as skip is reported and left "
                "untouched on both sides."
            )
        )

        self.banner = Banner()
        layout.addWidget(self.banner)

        self.model = ConflictTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setItemDelegateForColumn(
            ConflictTableModel.DECISION_COLUMN, ResolutionDelegate(self)
        )
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(ConflictTableModel.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.model.dataChanged.connect(self._refresh_banner)
        layout.addWidget(self.table, 1)

        layout.addLayout(self._build_bulk_row())
        layout.addLayout(self._build_footer())

    def _build_bulk_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(Muted("Apply to selection:"))
        for label, resolution in [
            ("Replace with source", Resolution.OVERWRITE),
            ("Keep target", Resolution.KEEP_TARGET),
            ("Keep both", Resolution.KEEP_BOTH),
            ("Skip", Resolution.UNRESOLVED),
        ]:
            button = QPushButton(label)
            button.clicked.connect(
                lambda _=False, value=resolution: self._apply_bulk(value)
            )
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        back = primary(QPushButton("Return to review"))
        back.setDefault(True)
        back.clicked.connect(self.done.emit)
        row.addWidget(back)
        return row

    def _apply_bulk(self, resolution: Resolution) -> None:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not rows:  # nothing selected: treat as "apply to all", which is the
            rows = list(range(self.model.rowCount()))  # common case for bulk use
        self.model.apply_to_rows(rows, resolution)
        self._refresh_banner()

    def set_items(self, items: list[PlanItem]) -> None:
        self.model.set_items(items)
        self._refresh_banner()

    def _refresh_banner(self, *_) -> None:
        total = self.model.rowCount()
        unresolved = self.model.unresolved_count()
        if not total:
            self.banner.show_message("No conflicts to resolve.", "success")
        elif unresolved:
            self.banner.show_message(
                f"{unresolved} of {total} conflict(s) will be skipped. Select "
                "rows and apply a decision, or leave them — skipped files are "
                "left untouched and listed in the report.",
                "warning",
            )
        else:
            self.banner.show_message(
                f"All {total} conflict(s) have a decision.", "success"
            )
