"""The NASSync dark theme: design tokens, palette, and application stylesheet.

Everything visual is defined here so the rest of the GUI can stay declarative.
The theme is applied unconditionally rather than following the system setting --
a migration tool is stared at for hours, often in a server room, and a single
predictable dark surface is easier to read than whatever the desktop happens to
be set to.

The stylesheet uses :class:`string.Template` (``$name``) rather than
``str.format``, because CSS is made almost entirely of braces and escaping every
one of them makes the source unreadable.
"""

from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QPalette

#: Design tokens. Change a value here and it propagates everywhere.
TOKENS: dict[str, str] = {
    # Surfaces, from furthest back to closest
    "canvas": "#0D1117",
    "surface": "#161B22",
    "raised": "#1C2128",
    "overlay": "#22272E",
    # Lines
    "border": "#30363D",
    "border_subtle": "#21262D",
    "border_strong": "#484F58",
    # Text
    "text": "#E6EDF3",
    "text_muted": "#9198A1",
    "text_faint": "#6E7681",
    "text_on_accent": "#FFFFFF",
    # Accent and status
    "accent": "#1F6FEB",
    "accent_hover": "#388BFD",
    "accent_pressed": "#1A5FCC",
    "accent_soft": "rgba(56, 139, 253, 0.14)",
    "success": "#3FB950",
    "success_soft": "rgba(63, 185, 80, 0.14)",
    "warning": "#D29922",
    "warning_soft": "rgba(210, 153, 34, 0.14)",
    "danger": "#F85149",
    "danger_soft": "rgba(248, 81, 73, 0.14)",
    # Metrics
    "radius": "6px",
    "radius_lg": "10px",
    "font": '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif',
    "font_mono": '"Cascadia Mono", Consolas, "Courier New", monospace',
}

#: Row tints for the plan table, as QColor rather than CSS. Alpha is set high
#: enough to read against the dark surface *and* the alternating row colour --
#: the low values that work on a light theme vanish completely here.
ROW_TINTS = {
    "delete": QColor(248, 81, 73, 46),
    "delete_dir": QColor(248, 81, 73, 62),
    "conflict": QColor(210, 153, 34, 52),
    "unsyncable": QColor(110, 118, 129, 42),
}

STATUS_COLOURS = {
    "accent": QColor("#58A6FF"),
    "success": QColor("#3FB950"),
    "warning": QColor("#D29922"),
    "danger": QColor("#F85149"),
    "muted": QColor("#9198A1"),
    "text": QColor("#E6EDF3"),
}


def palette() -> QPalette:
    """A dark QPalette, so native-drawn parts match the stylesheet."""
    p = QPalette()
    p.setColor(QPalette.Window, QColor(TOKENS["canvas"]))
    p.setColor(QPalette.WindowText, QColor(TOKENS["text"]))
    p.setColor(QPalette.Base, QColor(TOKENS["surface"]))
    p.setColor(QPalette.AlternateBase, QColor(TOKENS["raised"]))
    p.setColor(QPalette.Text, QColor(TOKENS["text"]))
    p.setColor(QPalette.Button, QColor(TOKENS["raised"]))
    p.setColor(QPalette.ButtonText, QColor(TOKENS["text"]))
    p.setColor(QPalette.Highlight, QColor(TOKENS["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(TOKENS["text_on_accent"]))
    p.setColor(QPalette.ToolTipBase, QColor(TOKENS["overlay"]))
    p.setColor(QPalette.ToolTipText, QColor(TOKENS["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(TOKENS["text_faint"]))
    p.setColor(QPalette.Link, QColor(TOKENS["accent_hover"]))
    for group in (QPalette.Disabled,):
        p.setColor(group, QPalette.Text, QColor(TOKENS["text_faint"]))
        p.setColor(group, QPalette.ButtonText, QColor(TOKENS["text_faint"]))
        p.setColor(group, QPalette.WindowText, QColor(TOKENS["text_faint"]))
    return p


_STYLESHEET = Template("""
* { font-family: $font; }

QWidget {
    background: $canvas;
    color: $text;
    font-size: 13px;
}

QMainWindow, QDialog { background: $canvas; }

/* Labels must never paint their own background, or every one of them shows
   as a canvas-coloured band when it sits on a card or a banner. */
QLabel { background: transparent; }

/* ---------- typography ---------- */
QLabel#PageTitle    { font-size: 21px; font-weight: 600; color: $text; }
QLabel#PageSubtitle { font-size: 13px; color: $text_muted; }
QLabel#SectionTitle { font-size: 13px; font-weight: 600; color: $text; }
QLabel#Muted        { color: $text_muted; }
QLabel#Faint        { color: $text_faint; font-size: 12px; }
QLabel#WordMark     { font-size: 16px; font-weight: 600; letter-spacing: 0.3px; }

/* ---------- chrome ---------- */
QWidget#HeaderBar {
    background: $surface;
    border-bottom: 1px solid $border_subtle;
}
QWidget#StepRail {
    background: $surface;
    border-right: 1px solid $border_subtle;
}
QWidget#PageArea { background: $canvas; }

QMenuBar { background: $surface; color: $text_muted; border: none; }
QMenuBar::item { padding: 6px 10px; background: transparent; border-radius: $radius; }
QMenuBar::item:selected { background: $overlay; color: $text; }
QMenu {
    background: $overlay;
    border: 1px solid $border;
    border-radius: $radius;
    padding: 4px;
}
QMenu::item { padding: 6px 24px 6px 12px; border-radius: 4px; }
QMenu::item:selected { background: $accent; color: $text_on_accent; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }

QStatusBar { background: $surface; color: $text_muted; border-top: 1px solid $border_subtle; }
QStatusBar::item { border: none; }

QToolTip {
    background: $overlay;
    color: $text;
    border: 1px solid $border;
    border-radius: $radius;
    padding: 6px 8px;
}

/* ---------- cards ---------- */
QFrame#Card {
    background: $surface;
    border: 1px solid $border_subtle;
    border-radius: $radius_lg;
}
QFrame#Tile {
    background: $surface;
    border: 1px solid $border_subtle;
    border-radius: $radius_lg;
}
QLabel#TileValue   { font-size: 22px; font-weight: 600; background: transparent; }
QLabel#TileCaption { font-size: 11px; color: $text_muted; background: transparent; }
QFrame#Tile QLabel { background: transparent; }

QFrame#Banner { border-radius: $radius; }
QFrame#Banner QLabel { background: transparent; }

QFrame#Separator { background: $border_subtle; max-height: 1px; border: none; }

/* ---------- buttons ---------- */
QPushButton {
    background: $raised;
    color: $text;
    border: 1px solid $border;
    border-radius: $radius;
    padding: 7px 14px;
    font-weight: 500;
}
QPushButton:hover  { background: $overlay; border-color: $border_strong; }
QPushButton:pressed { background: $surface; }
QPushButton:disabled { color: $text_faint; background: $surface; border-color: $border_subtle; }

QPushButton[variant="primary"] {
    background: $accent;
    border: 1px solid $accent;
    color: $text_on_accent;
}
QPushButton[variant="primary"]:hover   { background: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background: $accent_pressed; }
QPushButton[variant="primary"]:disabled {
    background: $surface; border-color: $border_subtle; color: $text_faint;
}

QPushButton[variant="danger"] { color: $danger; border-color: $border; }
QPushButton[variant="danger"]:hover { background: $danger_soft; border-color: $danger; }

QPushButton[variant="ghost"] { background: transparent; border-color: transparent; }
QPushButton[variant="ghost"]:hover { background: $overlay; border-color: $border; }

QPushButton[variant="filter"] {
    background: transparent;
    border: 1px solid $border;
    border-radius: 14px;
    padding: 5px 13px;
    color: $text_muted;
    font-weight: 500;
}
QPushButton[variant="filter"]:hover { background: $overlay; color: $text; }
QPushButton[variant="filter"]:checked {
    background: $accent_soft;
    border-color: $accent;
    color: $accent_hover;
}

/* ---------- inputs ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: $canvas;
    border: 1px solid $border;
    border-radius: $radius;
    padding: 6px 9px;
    selection-background-color: $accent;
    selection-color: $text_on_accent;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: $accent;
}
QLineEdit:disabled, QComboBox:disabled { color: $text_faint; background: $surface; }

/* Once a combo box is styled at all, Qt stops drawing the native arrow, and a
   CSS border triangle renders as a flat square -- so the chevron is supplied
   as a real asset. */
QComboBox::drop-down { border: none; width: 22px; background: transparent; }
QComboBox::down-arrow { image: url($chevron); width: 10px; height: 6px; }
QComboBox QAbstractItemView {
    background: $overlay;
    border: 1px solid $border;
    border-radius: $radius;
    selection-background-color: $accent;
    selection-color: $text_on_accent;
    padding: 4px;
    outline: none;
}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: $raised; border: none; width: 16px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: $overlay; }

QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid $border_strong;
    border-radius: 4px;
    background: $canvas;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QCheckBox::indicator:disabled { border-color: $border_subtle; background: $surface; }

QGroupBox {
    background: $surface;
    border: 1px solid $border_subtle;
    border-radius: $radius_lg;
    margin-top: 12px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: $text_muted;
    font-weight: 600;
}

/* ---------- tabs ---------- */
QTabWidget::pane { border: 1px solid $border_subtle; border-radius: $radius_lg; top: -1px; }
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab {
    background: transparent;
    color: $text_muted;
    padding: 8px 16px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: $radius;
    border-top-right-radius: $radius;
}
QTabBar::tab:hover { color: $text; background: $surface; }
QTabBar::tab:selected {
    color: $text;
    background: $surface;
    border-color: $border_subtle;
    border-bottom-color: $surface;
}

/* ---------- tables ---------- */
QTableView, QTableWidget, QListWidget {
    background: $surface;
    alternate-background-color: $raised;
    border: 1px solid $border_subtle;
    border-radius: $radius_lg;
    gridline-color: $border_subtle;
    selection-background-color: $accent_soft;
    selection-color: $text;
    outline: none;
}
QTableView::item, QTableWidget::item { padding: 4px 6px; border: none; }
QTableView::item:selected, QTableWidget::item:selected { background: $accent_soft; color: $text; }

QHeaderView { background: transparent; }
QHeaderView::section {
    background: $raised;
    color: $text_muted;
    padding: 7px 8px;
    border: none;
    border-right: 1px solid $border_subtle;
    border-bottom: 1px solid $border;
    font-weight: 600;
}
QHeaderView::section:hover { color: $text; }
QTableCornerButton::section { background: $raised; border: none; }

/* ---------- progress ---------- */
QProgressBar {
    background: $raised;
    border: none;
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: $text_muted;
}
QProgressBar::chunk { background: $accent; border-radius: 5px; }
QProgressBar[variant="thin"] { height: 4px; }

/* ---------- scrollbars ---------- */
QScrollBar:vertical { background: transparent; width: 11px; margin: 0; }
QScrollBar::handle:vertical {
    background: $border_strong; border-radius: 5px; min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: $text_faint; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0; }
QScrollBar::handle:horizontal {
    background: $border_strong; border-radius: 5px; min-width: 28px;
}
QScrollBar::handle:horizontal:hover { background: $text_faint; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
""")


_CHEVRON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6" '
    'viewBox="0 0 10 6"><path d="M1 1 L5 5 L9 1" fill="none" stroke="{colour}" '
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def _chevron_path() -> str:
    """Write the drop-down chevron out as an asset and return its URL path.

    Qt stylesheets cannot take an inline or data-URI image, so this has to live
    on disk. It is written next to the other application state and refreshed on
    each launch, which keeps it in step with the token colour.
    """
    from ..config import app_dir

    assets = app_dir() / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    target = assets / "chevron-down.svg"
    target.write_text(
        _CHEVRON_SVG.format(colour=TOKENS["text_muted"]), encoding="utf-8"
    )
    return target.as_posix()  # Qt CSS wants forward slashes, even on Windows


def stylesheet() -> str:
    """The full application stylesheet with tokens substituted in."""
    return _STYLESHEET.substitute(TOKENS, chevron=_chevron_path())
