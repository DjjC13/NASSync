"""Entry point for the packaged NASSync executable.

PyInstaller needs a real script to start from -- it cannot use ``python -m``.
Running the built executable with no arguments opens the GUI, which is what a
double-click does; any arguments are handed to the same command line the
source install exposes.
"""

import sys

from nassync.cli import main

if __name__ == "__main__":
    sys.exit(main())
