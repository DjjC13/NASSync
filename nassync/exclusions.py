"""Exclusion rules, applied identically to source and target.

An excluded path is invisible to NASSync: it is never copied *and* never
deleted. That symmetry matters -- excluding ``.nassync-trash`` is what stops a
mirror run from deleting its own safety net.

Two kinds of pattern, distinguished by whether they contain a path separator:

``Thumbs.db``, ``@Recycle``, ``*.tmp``
    **Name patterns.** Matched against each file or folder name at any depth.
    A matching folder is never descended into, so its whole subtree is skipped.

``\\@Recycle``, ``Archive\\2019``, ``Projects\\*\\temp``
    **Path patterns.** Matched against the path relative to the share root, and
    against everything beneath it. Use these to exclude one specific folder
    rather than every folder that happens to share its name. A leading
    backslash is optional -- path patterns are always anchored at the share
    root.
"""

from __future__ import annotations

from fnmatch import fnmatch

#: Junk that should never migrate. Deliberately a named list rather than a
#: blanket "skip hidden files" rule, since hidden sometimes hides something real.
DEFAULT_EXCLUSIONS: tuple[str, ...] = (
    # NASSync's own safety net
    ".nassync-trash",
    # QNAP
    "@Recycle",
    "@Recently-Snapshot",
    "@Transcode",
    ".@__thumb",
    # Synology
    "#recycle",
    "@eaDir",
    "#snapshot",
    # Windows
    "$RECYCLE.BIN",
    "System Volume Information",
    "Thumbs.db",
    "desktop.ini",
    # macOS
    ".DS_Store",
    ".Trash*",
    "._*",
    # Applications
    "~$*",
    "*.tmp",
)


def _normalise(pattern: str) -> str:
    return pattern.strip().replace("/", "\\")


class ExclusionSet:
    """Matches names and paths against a list of case-insensitive patterns."""

    def __init__(self, patterns=DEFAULT_EXCLUSIONS):
        self.patterns = tuple(
            _normalise(p) for p in patterns if p and p.strip()
        )
        self._names: list[str] = []
        self._paths: list[str] = []
        for pattern in self.patterns:
            lowered = pattern.lower()
            if "\\" in lowered:
                self._paths.append(lowered.strip("\\"))
            else:
                self._names.append(lowered)

    def matches_name(self, name: str) -> bool:
        """True if a single file or folder name is excluded at any depth."""
        lowered = name.lower()
        return any(fnmatch(lowered, pattern) for pattern in self._names)

    def matches_relpath(self, relpath: str) -> bool:
        """True if *relpath* (relative to the share root) is excluded.

        Covers both pattern kinds, and treats a matching folder as excluding
        everything beneath it.
        """
        lowered = _normalise(relpath).strip("\\").lower()
        if not lowered:
            return False

        for component in lowered.split("\\"):
            if any(fnmatch(component, pattern) for pattern in self._names):
                return True

        for pattern in self._paths:
            # The folder itself, or anything inside it.
            if fnmatch(lowered, pattern) or fnmatch(lowered, pattern + "\\*"):
                return True
        return False

    def excludes(self, relpath: str, name: str) -> bool:
        """Fast path for the scanner, which already knows both parts.

        Checks the name first because that is the common case and needs no
        string splitting; only falls back to full-path matching when path
        patterns are actually configured.
        """
        if self.matches_name(name):
            return True
        if not self._paths:
            return False
        lowered = _normalise(relpath).strip("\\").lower()
        return any(
            fnmatch(lowered, pattern) or fnmatch(lowered, pattern + "\\*")
            for pattern in self._paths
        )

    def __len__(self) -> int:
        return len(self.patterns)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ExclusionSet({self.patterns!r})"
