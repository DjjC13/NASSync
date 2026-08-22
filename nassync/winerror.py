"""Win32 error codes, translated into something an operator can act on.

Shared by share enumeration and sign-in so both report failures the same way,
and so both agree on which failures mean "this needs credentials".
"""

from __future__ import annotations

import ctypes

# Errors that mean the connection itself was refused for who you are, rather
# than because the server or path is wrong. These are the ones worth offering
# a username and password for.
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PASSWORD = 86
ERROR_SESSION_CREDENTIAL_CONFLICT = 1219
ERROR_LOGON_FAILURE = 1326
ERROR_ACCOUNT_RESTRICTION = 1327
ERROR_PASSWORD_EXPIRED = 1330
ERROR_ACCOUNT_DISABLED = 1331
ERROR_PASSWORD_MUST_CHANGE = 1907
ERROR_BAD_USERNAME = 2202

AUTH_ERROR_CODES = frozenset(
    {
        ERROR_ACCESS_DENIED,
        ERROR_INVALID_PASSWORD,
        ERROR_SESSION_CREDENTIAL_CONFLICT,
        ERROR_LOGON_FAILURE,
        ERROR_ACCOUNT_RESTRICTION,
        ERROR_PASSWORD_EXPIRED,
        ERROR_ACCOUNT_DISABLED,
        ERROR_PASSWORD_MUST_CHANGE,
        ERROR_BAD_USERNAME,
    }
)

_MESSAGES = {
    ERROR_ACCESS_DENIED: "access denied — this account is not permitted to connect",
    ERROR_INVALID_PASSWORD: "the password was not accepted",
    ERROR_SESSION_CREDENTIAL_CONFLICT: (
        "Windows already has a connection to this server using different "
        "credentials"
    ),
    ERROR_LOGON_FAILURE: "the user name or password is incorrect",
    ERROR_ACCOUNT_RESTRICTION: (
        "the account is restricted — it may have no password set, or be barred "
        "from signing in at this time"
    ),
    ERROR_PASSWORD_EXPIRED: "the password has expired",
    ERROR_ACCOUNT_DISABLED: "the account is disabled",
    ERROR_PASSWORD_MUST_CHANGE: "the password must be changed before it can be used",
    ERROR_BAD_USERNAME: "the user name is not in a form the server accepts",
    53: "network path not found — check the server name and that it is online",
    67: "network name not found — the server rejected the connection",
    1202: "the connection could not be made with the name given",
    2114: "the Server service is not running on that host",
}


def is_auth_error(code: int) -> bool:
    """True when supplying a username and password could plausibly help."""
    return code in AUTH_ERROR_CODES


def error_text(code: int) -> str:
    """A readable explanation for a Win32 error code."""
    if code in _MESSAGES:
        return _MESSAGES[code]
    return ctypes.FormatError(code).strip() or f"Windows error {code}"
