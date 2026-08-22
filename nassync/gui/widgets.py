"""Reusable presentation widgets, all styled from :mod:`nassync.gui.theme`."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .branding import logo_pixmap
from .theme import TOKENS


#: Shared by the execution and results pages, both of which can be showing a
#: verification pass in progress.
VERIFY_PHASE_LABELS = {
    "scan-source": "Verifying — re-reading the source",
    "scan-target": "Verifying — re-reading the target",
    "compare": "Verifying — comparing both sides",
    "done": "Verifying — finishing",
}


def verification_status(event) -> str:
    """One line describing a verification pass in flight."""
    label = VERIFY_PHASE_LABELS.get(event.phase, "Verifying")
    if event.files or event.dirs:
        label = f"{label} — {event.files:,} files, {event.dirs:,} folders read"
    return label


def primary(button: QPushButton) -> QPushButton:
    """Mark a button as the page's main action."""
    button.setProperty("variant", "primary")
    return button


def variant(button: QPushButton, name: str) -> QPushButton:
    """Apply a styled button variant: ghost, danger, filter."""
    button.setProperty("variant", name)
    return button


class PageTitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("PageTitle")


class PageSubtitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("PageSubtitle")
        self.setWordWrap(True)


class Muted(QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("Muted")
        self.setWordWrap(True)


class SectionTitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("SectionTitle")


class Card(QFrame):
    """A bordered surface that groups related controls."""

    def __init__(self, parent=None, margins=(16, 14, 16, 14), spacing=10):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)

    def add(self, widget) -> None:
        self.body.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


class StatTile(QFrame):
    """A single figure with a caption."""

    def __init__(self, caption: str, value: str = "0", parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumWidth(112)

        self._value = QLabel(value)
        self._value.setObjectName("TileValue")
        self._caption = QLabel(caption)
        self._caption.setObjectName("TileCaption")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 11)
        layout.setSpacing(1)
        layout.addWidget(self._value)
        layout.addWidget(self._caption)

    def set_value(self, value) -> None:
        self._value.setText(str(value))

    def set_emphasis(self, token: str | None) -> None:
        """Tint the figure using a theme colour token, or clear it."""
        colour = TOKENS.get(token or "", "")
        self._value.setStyleSheet(
            f"color: {colour}; background: transparent;" if colour else "background: transparent;"
        )


class TileRow(QWidget):
    """A strip of :class:`StatTile`, addressable by key."""

    def __init__(self, captions: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.tiles: dict[str, StatTile] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        for key, caption in captions:
            tile = StatTile(caption)
            self.tiles[key] = tile
            layout.addWidget(tile)
        layout.addStretch(1)

    def set(self, key: str, value) -> None:
        if key in self.tiles:
            self.tiles[key].set_value(value)

    def emphasise(self, key: str, token: str | None) -> None:
        if key in self.tiles:
            self.tiles[key].set_emphasis(token)


class Banner(QFrame):
    """A message strip carrying a severity."""

    TONES = {
        "info": ("border", "surface", "text_muted"),
        "accent": ("accent", "accent_soft", "text"),
        "success": ("success", "success_soft", "text"),
        "warning": ("warning", "warning_soft", "text"),
        "danger": ("danger", "danger_soft", "text"),
    }

    def __init__(self, text: str = "", tone: str = "info", parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")

        self._icon = QLabel()
        self._icon.setFixedWidth(4)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.addWidget(self._label, 1)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        border, background, text = self.TONES.get(tone, self.TONES["info"])
        self.setStyleSheet(
            f"QFrame#Banner {{"
            f" border: 1px solid {TOKENS[border]};"
            f" border-left: 3px solid {TOKENS[border]};"
            f" border-radius: {TOKENS['radius']};"
            f" background: {TOKENS[background]}; }}"
            f"QFrame#Banner QLabel {{ color: {TOKENS[text]}; background: transparent; }}"
        )

    def show_message(self, text: str, tone: str = "info") -> None:
        self._label.setText(text)
        self.set_tone(tone)
        self.setVisible(bool(text))


class LogView(QPlainTextEdit):
    """Append-only monospaced log with a bounded backlog.

    Lines are tinted by their leading keyword so a failure is obvious in a wall
    of successful copies.
    """

    _TONES = {
        "FAILED": "danger",
        "ERROR": "danger",
        "KEPT": "warning",
        "SKIPPED": "warning",
        "REMOVED": "warning",
        "COPIED": "text_muted",
        "UPDATED": "text_muted",
        "CREATED": "text_muted",
    }

    def __init__(self, parent=None, max_lines: int = 5000):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Cascadia Mono")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self.setFont(font)

    def append_line(self, text: str) -> None:
        keyword = text.split(" ", 1)[0].strip()
        token = self._TONES.get(keyword)
        if token:
            escaped = html.escape(text).replace(" ", "&nbsp;")
            self.appendHtml(f'<span style="color:{TOKENS[token]}">{escaped}</span>')
        else:
            self.appendPlainText(text)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class HeaderBar(QWidget):
    """Application header: mark, wordmark, and the active context."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HeaderBar")
        self.setFixedHeight(52)

        mark = QLabel()
        mark.setPixmap(logo_pixmap(26, self.devicePixelRatioF()))
        mark.setFixedWidth(30)
        mark.setStyleSheet("background: transparent;")

        wordmark = QLabel("NASSync")
        wordmark.setObjectName("WordMark")
        wordmark.setStyleSheet("background: transparent;")

        self.context = QLabel("")
        self.context.setObjectName("Faint")
        self.context.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.context.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)
        layout.addWidget(mark)
        layout.addWidget(wordmark)
        layout.addStretch(1)
        layout.addWidget(self.context)

    def set_context(self, text: str) -> None:
        self.context.setText(text)


class StepItem(QWidget):
    """One entry in the step rail."""

    def __init__(self, number: int, title: str, parent=None):
        super().__init__(parent)
        self.number = number
        self._title = title

        self.badge = QLabel(str(number))
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedSize(24, 24)

        self.label = QLabel(title)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(11)
        layout.addWidget(self.badge)
        layout.addWidget(self.label, 1)
        self.set_state("upcoming")

    def set_state(self, state: str) -> None:
        """*state* is one of: done, current, upcoming."""
        if state == "current":
            badge = (
                f"background: {TOKENS['accent']}; color: {TOKENS['text_on_accent']};"
                f" border: none;"
            )
            text = f"color: {TOKENS['text']}; font-weight: 600; background: transparent;"
            row = (
                f"background: {TOKENS['accent_soft']};"
                f" border-left: 2px solid {TOKENS['accent']};"
            )
            self.badge.setText(str(self.number))
        elif state == "done":
            badge = (
                f"background: {TOKENS['success_soft']}; color: {TOKENS['success']};"
                f" border: 1px solid {TOKENS['success']};"
            )
            text = f"color: {TOKENS['text_muted']}; background: transparent;"
            row = "background: transparent; border-left: 2px solid transparent;"
            self.badge.setText("✓")
        else:
            badge = (
                f"background: transparent; color: {TOKENS['text_faint']};"
                f" border: 1px solid {TOKENS['border']};"
            )
            text = f"color: {TOKENS['text_faint']}; background: transparent;"
            row = "background: transparent; border-left: 2px solid transparent;"
            self.badge.setText(str(self.number))

        self.badge.setStyleSheet(badge + " border-radius: 12px; font-size: 11px;")
        self.label.setStyleSheet(text)
        self.setStyleSheet(f"StepItem {{ {row} }}")


class StepRail(QWidget):
    """Left-hand progress rail showing where the operator is in the workflow."""

    def __init__(self, steps: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("StepRail")
        self.setFixedWidth(196)

        heading = QLabel("WORKFLOW")
        heading.setStyleSheet(
            f"color: {TOKENS['text_faint']}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 1px; background: transparent;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 16)
        layout.setSpacing(2)

        heading_wrap = QHBoxLayout()
        heading_wrap.setContentsMargins(14, 0, 14, 8)
        heading_wrap.addWidget(heading)
        layout.addLayout(heading_wrap)

        self.items: list[StepItem] = []
        for index, title in enumerate(steps, start=1):
            item = StepItem(index, title)
            self.items.append(item)
            layout.addWidget(item)
        layout.addStretch(1)

    def set_current(self, index: int) -> None:
        """Highlight step *index* (0-based); everything before it reads as done."""
        for position, item in enumerate(self.items):
            if position < index:
                item.set_state("done")
            elif position == index:
                item.set_state("current")
            else:
                item.set_state("upcoming")
