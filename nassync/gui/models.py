"""Qt table models for plan items, conflicts, and failures."""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate

from ..model import Action, ItemState, PlanItem, Resolution
from ..paths import human_bytes
from .theme import ROW_TINTS, STATUS_COLOURS

#: Colour of the Action cell, so the table can be scanned by hue alone.
_ACTION_COLOURS = {
    Action.COPY: STATUS_COLOURS["success"],
    Action.OVERWRITE: STATUS_COLOURS["accent"],
    Action.MKDIR: STATUS_COLOURS["muted"],
    Action.DELETE: STATUS_COLOURS["danger"],
    Action.DELETE_DIR: STATUS_COLOURS["danger"],
    Action.CONFLICT: STATUS_COLOURS["warning"],
    Action.UNSYNCABLE: STATUS_COLOURS["muted"],
}

#: Row tints. Destructive actions are deliberately the most visible thing in
#: the preview -- they are the only ones that can lose data.
_ROW_COLOURS = {
    Action.DELETE: ROW_TINTS["delete"],
    Action.DELETE_DIR: ROW_TINTS["delete_dir"],
    Action.CONFLICT: ROW_TINTS["conflict"],
    Action.UNSYNCABLE: ROW_TINTS["unsyncable"],
}

ACTION_LABELS = {
    Action.COPY: "Copy",
    Action.OVERWRITE: "Update",
    Action.MKDIR: "Create folder",
    Action.DELETE: "Remove",
    Action.DELETE_DIR: "Remove folder",
    Action.CONFLICT: "Conflict",
    Action.UNSYNCABLE: "Unsupported",
    Action.SKIP: "Skip",
}

RESOLUTION_LABELS = {
    Resolution.UNRESOLVED: "Skip — decide later",
    Resolution.OVERWRITE: "Replace with source",
    Resolution.KEEP_TARGET: "Keep target version",
    Resolution.KEEP_BOTH: "Keep both versions",
}


def _when(value: float | None) -> str:
    if not value:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))


class PlanTableModel(QAbstractTableModel):
    """Every planned change, with a tick box per row."""

    COLUMNS = ["Action", "Path", "Size", "Source modified", "Target modified", "Details"]

    def __init__(self, items: list[PlanItem] | None = None, parent=None):
        super().__init__(parent)
        self._items: list[PlanItem] = items or []

    def set_items(self, items: list[PlanItem]) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def item_at(self, row: int) -> PlanItem:
        return self._items[row]

    # --- Qt interface -------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        item = self._items[index.row()]
        # Unsyncable rows cannot be acted on, so they cannot be ticked either.
        if index.column() == 0 and item.action is not Action.UNSYNCABLE:
            base |= Qt.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        column = index.column()

        if role == Qt.CheckStateRole and column == 0:
            if item.action is Action.UNSYNCABLE:
                return None
            return Qt.Checked if item.selected else Qt.Unchecked

        if role == Qt.BackgroundRole:
            colour = _ROW_COLOURS.get(item.action)
            return QBrush(colour) if colour else None

        if role == Qt.ForegroundRole and column == 0:
            colour = _ACTION_COLOURS.get(item.action)
            return QBrush(colour) if colour else None

        if role == Qt.ToolTipRole:
            return f"{item.pair_key}\n{item.relpath}\n{item.note}".strip()

        if role == Qt.DisplayRole:
            if column == 0:
                return ACTION_LABELS.get(item.action, item.action.value)
            if column == 1:
                return item.relpath
            if column == 2:
                return human_bytes(item.size) if item.size else ""
            if column == 3:
                return _when(item.source_mtime)
            if column == 4:
                return _when(item.target_mtime)
            if column == 5:
                return item.note

        # Sort sizes numerically rather than as "9 B" < "80 KB" strings.
        if role == Qt.UserRole and column == 2:
            return item.size
        return None

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:
        if role == Qt.CheckStateRole and index.column() == 0:
            item = self._items[index.row()]
            if item.action is Action.UNSYNCABLE:
                return False
            item.selected = Qt.CheckState(value) == Qt.Checked
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    def set_all_selected(self, rows: list[int], selected: bool) -> None:
        """Tick or untick many rows at once, from the preview's bulk buttons."""
        if not rows:
            return
        for row in rows:
            item = self._items[row]
            if item.action is not Action.UNSYNCABLE:
                item.selected = selected
        self.dataChanged.emit(
            self.index(min(rows), 0), self.index(max(rows), 0), [Qt.CheckStateRole]
        )


class PlanFilterProxy(QSortFilterProxyModel):
    """Filters the preview by action category and a free-text path match."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._actions: set[Action] | None = None
        self._text = ""
        self.setSortRole(Qt.DisplayRole)

    # begin/endFilterChange is the current Qt idiom; invalidateFilter and
    # invalidateRowsFilter are both deprecated, and plain invalidate() would
    # also throw away the sort order on every keystroke.
    def set_action_filter(self, actions: set[Action] | None) -> None:
        self.beginFilterChange()
        self._actions = actions
        self.endFilterChange()

    def set_text_filter(self, text: str) -> None:
        self.beginFilterChange()
        self._text = text.strip().lower()
        self.endFilterChange()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        model: PlanTableModel = self.sourceModel()
        item = model.item_at(row)
        if self._actions is not None and item.action not in self._actions:
            return False
        if self._text and self._text not in item.relpath.lower():
            return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        if left.column() == 2:  # size column sorts by bytes
            model: PlanTableModel = self.sourceModel()
            return model.item_at(left.row()).size < model.item_at(right.row()).size
        return super().lessThan(left, right)


class ConflictTableModel(QAbstractTableModel):
    """Files newer on the target, with a per-row decision."""

    COLUMNS = ["Path", "Source modified", "Target modified", "Size", "Decision"]
    DECISION_COLUMN = 4

    def __init__(self, items: list[PlanItem] | None = None, parent=None):
        super().__init__(parent)
        self._items: list[PlanItem] = items or []

    def set_items(self, items: list[PlanItem]) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def item_at(self, row: int) -> PlanItem:
        return self._items[row]

    @property
    def items(self) -> list[PlanItem]:
        return self._items

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == self.DECISION_COLUMN:
            base |= Qt.ItemIsEditable
        return base

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        column = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if column == 0:
                return item.relpath
            if column == 1:
                return _when(item.source_mtime)
            if column == 2:
                return _when(item.target_mtime)
            if column == 3:
                return human_bytes(item.size) if item.size else ""
            if column == self.DECISION_COLUMN:
                return RESOLUTION_LABELS[item.resolution]

        if role == Qt.BackgroundRole and item.resolution is Resolution.UNRESOLVED:
            return QBrush(_ROW_COLOURS[Action.CONFLICT])

        if role == Qt.ToolTipRole:
            return item.note
        return None

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:
        if role != Qt.EditRole or index.column() != self.DECISION_COLUMN:
            return False
        for resolution, label in RESOLUTION_LABELS.items():
            if label == value:
                self._items[index.row()].resolution = resolution
                self.dataChanged.emit(index, index)
                return True
        return False

    def apply_to_rows(self, rows: list[int], resolution: Resolution) -> None:
        """Bulk-apply one decision, from the 'apply to selected' buttons."""
        if not rows:
            return
        for row in rows:
            self._items[row].resolution = resolution
        self.dataChanged.emit(
            self.index(min(rows), 0), self.index(max(rows), self.DECISION_COLUMN)
        )

    def unresolved_count(self) -> int:
        return sum(1 for i in self._items if i.resolution is Resolution.UNRESOLVED)


class ResolutionDelegate(QStyledItemDelegate):
    """Drop-down editor for the conflict decision column."""

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        editor.addItems(list(RESOLUTION_LABELS.values()))
        return editor

    def setEditorData(self, editor: QComboBox, index) -> None:
        editor.setCurrentText(index.data(Qt.EditRole))

    def setModelData(self, editor: QComboBox, model, index) -> None:
        model.setData(index, editor.currentText(), Qt.EditRole)


class FailureTableModel(QAbstractTableModel):
    """Items that could not be copied or removed, and why."""

    COLUMNS = ["Status", "Path", "Attempts", "Reason"]

    def __init__(self, items: list[PlanItem] | None = None, parent=None):
        super().__init__(parent)
        self._items: list[PlanItem] = items or []

    def set_items(self, items: list[PlanItem]) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def item_at(self, row: int) -> PlanItem:
        return self._items[row]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        item = self._items[index.row()]
        return [
            "Abandoned" if item.state is ItemState.ABANDONED else "Failed",
            item.relpath,
            str(item.attempts),
            item.note,
        ][index.column()]
