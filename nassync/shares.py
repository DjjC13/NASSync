"""Live SMB share enumeration via the Windows NetShareEnum API.

Deliberately *not* implemented by parsing ``net view`` output -- that output is
localised, so parsing it silently returns nothing useful on a non-English
Windows install. Calling netapi32 directly is both locale-proof and faster.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from .winerror import error_text, is_auth_error

# Share types returned in SHARE_INFO_1.shi1_type.
_STYPE_DISKTREE = 0x00000000
_STYPE_MASK = 0x0000000F
_STYPE_SPECIAL = 0x80000000  # admin shares: C$, ADMIN$, IPC$
_STYPE_TEMPORARY = 0x40000000

_MAX_PREFERRED_LENGTH = 0xFFFFFFFF
_NERR_SUCCESS = 0
_ERROR_MORE_DATA = 234


class _ShareInfo1(ctypes.Structure):
    _fields_ = [
        ("shi1_netname", wintypes.LPWSTR),
        ("shi1_type", wintypes.DWORD),
        ("shi1_remark", wintypes.LPWSTR),
    ]


@dataclass(frozen=True)
class ShareInfo:
    """One disk share exposed by a server."""

    name: str
    remark: str = ""

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.name


class ShareEnumerationError(RuntimeError):
    """Raised when a server cannot be reached or refuses enumeration.

    ``needs_credentials`` distinguishes "we do not know who you are" from
    "that server is not there", so the GUI only offers a sign-in form for
    failures a username and password could actually fix.
    """

    def __init__(self, message: str, code: int = 0, needs_credentials: bool = False):
        super().__init__(message)
        self.code = code
        self.needs_credentials = needs_credentials


def list_shares(server: str, include_special: bool = False) -> list[ShareInfo]:
    """Enumerate disk shares on *server* using the caller's Windows credentials.

    Args:
        server: Host name, with or without leading backslashes.
        include_special: Include admin shares such as ``C$`` and ``IPC$``.

    Raises:
        ShareEnumerationError: if the server cannot be reached or enumerated.
    """
    server = server.strip().strip("\\/")
    if not server:
        raise ShareEnumerationError("No server name given.")

    netapi32 = ctypes.WinDLL("netapi32.dll")
    netapi32.NetShareEnum.restype = wintypes.DWORD
    netapi32.NetApiBufferFree.argtypes = [wintypes.LPVOID]

    buffer_ptr = ctypes.POINTER(_ShareInfo1)()
    entries_read = wintypes.DWORD(0)
    total_entries = wintypes.DWORD(0)
    resume_handle = wintypes.DWORD(0)

    shares: list[ShareInfo] = []
    while True:
        status = netapi32.NetShareEnum(
            wintypes.LPWSTR("\\\\" + server),
            wintypes.DWORD(1),
            ctypes.byref(buffer_ptr),
            wintypes.DWORD(_MAX_PREFERRED_LENGTH),
            ctypes.byref(entries_read),
            ctypes.byref(total_entries),
            ctypes.byref(resume_handle),
        )
        if status not in (_NERR_SUCCESS, _ERROR_MORE_DATA):
            raise ShareEnumerationError(
                f"Could not list shares on \\\\{server}: {error_text(status)}",
                status,
                needs_credentials=is_auth_error(status),
            )

        try:
            for i in range(entries_read.value):
                entry = buffer_ptr[i]
                share_type = entry.shi1_type
                is_special = bool(share_type & (_STYPE_SPECIAL | _STYPE_TEMPORARY))
                if (share_type & _STYPE_MASK) != _STYPE_DISKTREE:
                    continue
                if is_special and not include_special:
                    continue
                shares.append(
                    ShareInfo(name=entry.shi1_netname, remark=entry.shi1_remark or "")
                )
        finally:
            if buffer_ptr:
                netapi32.NetApiBufferFree(buffer_ptr)
                buffer_ptr = ctypes.POINTER(_ShareInfo1)()

        if status != _ERROR_MORE_DATA:
            break

    shares.sort(key=lambda s: s.name.lower())
    return shares


def auto_map(source: list[ShareInfo], target: list[ShareInfo]) -> dict[str, str | None]:
    """Pair source shares with same-named target shares, case-insensitively.

    Returns a mapping of source share name to target share name, with ``None``
    where no obvious counterpart exists -- those need the operator to choose.
    """
    by_lower = {s.name.lower(): s.name for s in target}
    return {s.name: by_lower.get(s.name.lower()) for s in source}
