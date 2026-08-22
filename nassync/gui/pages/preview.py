"""Step 3 -- the review. Nothing has been written at this point.

This is the page that has to earn the operator's trust: it must make the
destructive half of a mirror impossible to miss, and let any individual row be
excluded before the run starts.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...model import Action, Plan
from ...paths import human_bytes
from ..models import PlanFilterProxy, PlanTableModel
from ..widgets import Banner, Muted, PageSubtitle, PageTitle, TileRow, primary, variant

#: Filter chips across the top of the table.
_FILTERS: list[tuple[str, set[Action] | None]] = [
    ("All", None),
    ("New", {Action.COPY}),
    ("Updated", {Action.OVERWRITE}),
    ("Removals", {Action.DELETE, Action.DELETE_DIR}),
    ("Conflicts", {Action.CONFLICT}),
    ("Folders", {Action.MKDIR}),
    ("Unsupported", {Action.UNSYNCABLE}),
]


class PreviewPage(QWidget):
    """Summary tiles over a filterable table of every planned change."""

    back_requested = Signal()
    conflicts_requested = Signal()
    export_requested = Signal()
    start_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan: Plan | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(PageTitle("Review changes"))
        layout.addWidget(
            PageSubtitle(
                "Nothing has been written yet. Clear any row to leave it out of "
                "this run."
            )
        )

        self.tiles = TileRow(
            [
                ("identical", "Already identical"),
                ("copy", "New"),
                ("overwrite", "Updated"),
                ("delete", "Removals"),
                ("conflict", "Conflicts"),
                ("bytes", "Data to transfer"),
            ]
        )
        layout.addWidget(self.tiles)

        self.banner = Banner()
        self.banner.setVisible(False)
        layout.addWidget(self.banner)

        # The model must exist before the filter row, which wires straight to it.
        self.model = PlanTableModel()
        self.proxy = PlanFilterProxy()
        self.proxy.setSourceModel(self.model)

        layout.addLayout(self._build_filter_row())

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(25)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in range(2, len(PlanTableModel.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        layout.addLayout(self._build_selection_row())
        layout.addLayout(self._build_footer())

        # Wired last: these handlers touch widgets built above, and connecting
        # them earlier lets a model signal fire before those widgets exist.
        # Connected here rather than in set_plan, which runs again on every rescan.
        self.model.dataChanged.connect(self._refresh_counts)
        self.proxy.layoutChanged.connect(self._refresh_visible_label)

    # --- construction -------------------------------------------------------

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for index, (label, _actions) in enumerate(_FILTERS):
            button = variant(QPushButton(label), "filter")
            button.setCheckable(True)
            button.setChecked(index == 0)
            self._filter_group.addButton(button, index)
            row.addWidget(button)
        self._filter_group.idClicked.connect(
            lambda index: self.proxy.set_action_filter(_FILTERS[index][1])
        )

        row.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by path…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(280)
        self.search.textChanged.connect(self.proxy.set_text_filter)
        row.addWidget(self.search)
        return row

    def _build_selection_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.visible_label = Muted("")
        row.addWidget(self.visible_label)
        row.addStretch(1)

        include = variant(QPushButton("Include shown"), "ghost")
        exclude = variant(QPushButton("Exclude shown"), "ghost")
        include.setToolTip("Include every row currently visible in this run")
        exclude.setToolTip("Leave every row currently visible out of this run")
        include.clicked.connect(lambda: self._set_visible_selected(True))
        exclude.clicked.connect(lambda: self._set_visible_selected(False))
        row.addWidget(include)
        row.addWidget(exclude)
        return row

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        back = QPushButton("Back")
        back.clicked.connect(self.back_requested.emit)
        row.addWidget(back)

        export = QPushButton("Export plan…")
        export.clicked.connect(self.export_requested.emit)
        row.addWidget(export)
        row.addStretch(1)

        self.conflicts_button = QPushButton("Resolve conflicts")
        self.conflicts_button.clicked.connect(self.conflicts_requested.emit)
        row.addWidget(self.conflicts_button)

        self.start_button = primary(QPushButton("Execute sync"))
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start_requested.emit)
        row.addWidget(self.start_button)
        return row

    # --- population ---------------------------------------------------------

    def set_plan(self, plan: Plan) -> None:
        self._plan = plan
        self.model.set_items(plan.items)
        self.refresh()

    def refresh(self) -> None:
        """Recompute the tiles and banner -- call after conflicts are resolved."""
        self._refresh_counts()
        self._refresh_visible_label()

    def _refresh_counts(self, *_) -> None:
        if self._plan is None:
            return
        counts = self._plan.counts()
        deletions = counts[Action.DELETE] + counts[Action.DELETE_DIR]

        self.tiles.set("identical", f"{self._plan.identical:,}")
        self.tiles.set("copy", f"{counts[Action.COPY]:,}")
        self.tiles.set("overwrite", f"{counts[Action.OVERWRITE]:,}")
        self.tiles.set("delete", f"{deletions:,}")
        self.tiles.set("conflict", f"{counts[Action.CONFLICT]:,}")
        self.tiles.set(
            "bytes", human_bytes(self._plan.bytes_for(Action.COPY, Action.OVERWRITE))
        )
        self.tiles.emphasise("delete", "danger" if deletions else None)
        self.tiles.emphasise("conflict", "warning" if counts[Action.CONFLICT] else None)

        self.conflicts_button.setEnabled(bool(counts[Action.CONFLICT]))
        selected_deletions = sum(
            1 for i in self._plan.items if i.action.is_destructive and i.selected
        )
        actionable = len(self._plan.actionable)
        self.start_button.setEnabled(bool(actionable))
        self.start_button.setText(
            f"Execute sync — {actionable:,} change{'s' if actionable != 1 else ''}"
            if actionable
            else "No changes to apply"
        )

        messages = []
        if selected_deletions:
            messages.append(
                f"{selected_deletions:,} item(s) will be removed from the target "
                "because they no longer exist on the source."
            )
        if counts[Action.CONFLICT]:
            messages.append(
                f"{counts[Action.CONFLICT]} file(s) are newer on the target; "
                "unresolved conflicts are skipped and reported."
            )
        if counts[Action.UNSYNCABLE]:
            messages.append(
                f"{counts[Action.UNSYNCABLE]} name(s) are not valid on Windows "
                "and will be reported rather than copied."
            )
        if self._plan.errors:
            messages.append(f"{len(self._plan.errors)} path(s) could not be read.")

        tone = "danger" if selected_deletions else ("warning" if messages else "info")
        self.banner.show_message("  ".join(messages), tone)

    def _refresh_visible_label(self) -> None:
        shown = self.proxy.rowCount()
        total = self.model.rowCount()
        self.visible_label.setText(
            f"Showing {shown:,} of {total:,} changes"
            if shown != total
            else f"{total:,} changes"
        )

    def _set_visible_selected(self, selected: bool) -> None:
        rows = [
            self.proxy.mapToSource(self.proxy.index(row, 0)).row()
            for row in range(self.proxy.rowCount())
        ]
        self.model.set_all_selected(rows, selected)
        self._refresh_counts()
