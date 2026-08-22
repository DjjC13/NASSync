"""PySide6 front end for NASSync.

The GUI never touches the filesystem directly -- every long operation runs in a
worker thread from :mod:`nassync.gui.workers` and reports back via signals, so
the window stays responsive while a million-file share is being scanned.
"""
