"""Signing in to an SMB server with an explicit username and password.

NASSync normally rides on whatever credentials Windows already has. When that
is not enough, this module opens an authenticated session to the server's
``IPC$`` share via ``WNetAddConnection2`` -- the same mechanism ``net use``
uses. Everything afterwards (share enumeration, scanning, and robocopy in its
own process) travels over that session without needing the password again.

Deliberate choices about handling the password:

* It is passed straight to the Win32 call and never stored, logged, written to
  a profile, or included in a report.
* The connection is created **without** ``CONNECT_UPDATE_PROFILE``, so Windows
  does not persist it. It lasts for this logon session only and is gone at sign
  out -- NASSync never writes a credential to disk.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .winerror import (
    ERROR_SESSION_CREDENTIAL_CONFLICT,
    error_text,
    is_auth_error,
)

_RESOURCETYPE_ANY = 0x00000000
_NO_ERROR = 0


class _NetResource(ctypes.Structure):
    _fields_ = [
        ("dwScope", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("dwDisplayType", wintypes.DWORD),
        ("dwUsage", wintypes.DWORD),
        ("lpLocalName", wintypes.LPWSTR),
        ("lpRemoteName", wintypes.LPWSTR),
        ("lpComment", wintypes.LPWSTR),
        ("lpProvider", wintypes.LPWSTR),
    ]


class AuthenticationError(RuntimeError):
    """Sign-in was refused. ``code`` is the underlying Win32 error."""

    def __init__(self, message: str, code: int = 0, needs_credentials: bool = True):
        super().__init__(message)
        self.code = code
        self.needs_credentials = needs_credentials


def _mpr():
    library = ctypes.WinDLL("mpr.dll")
    library.WNetAddConnection2W.restype = wintypes.DWORD
    library.WNetCancelConnection2W.restype = wintypes.DWORD
    return library


def _ipc_path(server: str) -> str:
    return "\\\\" + server.strip().strip("\\/") + "\\IPC$"


def sign_in(server: str, username: str, password: str) -> None:
    """Open an authenticated session to *server* for this logon session.

    Args:
        server: Host name, with or without leading backslashes.
        username: ``DOMAIN\\user``, ``user@domain``, or a local account name.
        password: Sent directly to Windows and not retained by NASSync.

    Raises:
        AuthenticationError: if the server refuses the credentials.
    """
    server = server.strip().strip("\\/")
    if not server:
        raise AuthenticationError("No server name given.", needs_credentials=False)
    if not username:
        raise AuthenticationError("Enter a user name to sign in with.")

    library = _mpr()
    resource = _NetResource(
        dwScope=0,
        dwType=_RESOURCETYPE_ANY,
        dwDisplayType=0,
        dwUsage=0,
        lpLocalName=None,
        lpRemoteName=_ipc_path(server),
        lpComment=None,
        lpProvider=None,
    )

    status = library.WNetAddConnection2W(
        ctypes.byref(resource), password, username, 0
    )

    if status == ERROR_SESSION_CREDENTIAL_CONFLICT:
        # Windows permits only one set of credentials per server per session.
        # The operator has explicitly asked to sign in as someone else, so drop
        # the existing session and try once more -- but without forcing, so an
        # open file elsewhere is never yanked out from under another program.
        if _cancel(library, server, force=False) == _NO_ERROR:
            status = library.WNetAddConnection2W(
                ctypes.byref(resource), password, username, 0
            )
        else:
            raise AuthenticationError(
                f"Windows already has a connection to \\\\{server} using different "
                "credentials, and it is in use. Close anything open on that "
                f"server, or run: net use \\\\{server} /delete",
                ERROR_SESSION_CREDENTIAL_CONFLICT,
            )

    if status != _NO_ERROR:
        raise AuthenticationError(
            f"Could not sign in to \\\\{server}: {error_text(status)}",
            status,
            needs_credentials=is_auth_error(status),
        )


def sign_out(server: str, force: bool = False) -> bool:
    """Close the session to *server*. Returns True if one was removed."""
    return _cancel(_mpr(), server.strip().strip("\\/"), force) == _NO_ERROR


def _cancel(library, server: str, force: bool) -> int:
    return library.WNetCancelConnection2W(_ipc_path(server), 0, bool(force))
