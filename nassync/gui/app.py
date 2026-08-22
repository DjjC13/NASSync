"""GUI entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .branding import logo_icon
from .main_window import MainWindow
from .theme import palette, stylesheet


def main() -> int:
    """Launch the NASSync window. Returns the Qt exit code."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("NASSync")
    app.setOrganizationName("NASSync")

    # Fusion is the only built-in style that honours a custom palette
    # consistently across Windows versions.
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    app.setWindowIcon(logo_icon())

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
