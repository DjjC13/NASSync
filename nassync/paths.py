"""UNC path construction and Windows path legality checks.

Every filesystem call in NASSync goes through :func:`extended` so that paths
longer than MAX_PATH (260) are handled without the caller having to care.
"""

from __future__ import annotations

import re

# Names Windows reserves regardless of extension (CON.txt is just as illegal).
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

# Characters that cannot appear in a Windows path component.
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_EXTENDED_PREFIX = "\\\\?\\"
_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"


def share_root(server: str, share: str) -> str:
    """Build ``\\\\server\\share`` from its parts."""
    return "\\\\" + server.strip("\\/") + "\\" + share.strip("\\/")


def join(root: str, relpath: str) -> str:
    """Join a relative path onto a root, tolerating empty or slash-y input."""
    relpath = relpath.replace("/", "\\").strip("\\")
    if not relpath:
        return root
    return root.rstrip("\\") + "\\" + relpath


def extended(path: str) -> str:
    """Return *path* in extended-length form so >260 char paths work.

    ``\\\\server\\share\\x`` becomes ``\\\\?\\UNC\\server\\share\\x``; a local
    path becomes ``\\\\?\\C:\\x``. Already-extended paths pass through.
    """
    if path.startswith(_EXTENDED_PREFIX):
        return path
    if path.startswith("\\\\"):
        return _EXTENDED_UNC_PREFIX + path[2:]
    return _EXTENDED_PREFIX + path


def plain(path: str) -> str:
    """Inverse of :func:`extended` -- for display and for handing to robocopy."""
    if path.startswith(_EXTENDED_UNC_PREFIX):
        return "\\\\" + path[len(_EXTENDED_UNC_PREFIX):]
    if path.startswith(_EXTENDED_PREFIX):
        return path[len(_EXTENDED_PREFIX):]
    return path


def split_unc(path: str) -> tuple[str, str, str]:
    """Split ``\\\\server\\share\\rest`` into ``(server, share, rest)``."""
    if not path.startswith("\\\\"):
        raise ValueError(f"not a UNC path: {path!r}")
    parts = path[2:].split("\\")
    if len(parts) < 2:
        raise ValueError(f"UNC path is missing a share name: {path!r}")
    return parts[0], parts[1], "\\".join(parts[2:])


def illegal_component(name: str) -> str | None:
    """Explain why *name* is unusable on Windows, or return None if it is fine.

    Both ends of a NASSync run are Linux SMB servers, which happily store names
    Windows cannot create locally. We surface these rather than let a copy fail
    with an opaque error mid-run.
    """
    if not name:
        return "empty name"
    if _ILLEGAL_CHARS.search(name):
        return "contains a character Windows disallows"
    if name.rstrip(". ") != name:
        return "ends with a dot or space"
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES:
        return f"'{stem}' is a reserved device name"
    return None


def illegal_relpath(relpath: str) -> str | None:
    """Run :func:`illegal_component` over every component of a relative path."""
    for component in relpath.replace("/", "\\").split("\\"):
        if not component:
            continue
        reason = illegal_component(component)
        if reason:
            return f"{component!r}: {reason}"
    return None


def human_bytes(count: float) -> str:
    """Format a byte count for display (1.4 GB, 812 MB, 0 B)."""
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(count) < step or unit == "TB":
            if unit == "B":
                return f"{int(count)} {unit}"
            return f"{count:.1f} {unit}"
        count /= step
    return f"{count:.1f} TB"  # pragma: no cover - loop always returns
