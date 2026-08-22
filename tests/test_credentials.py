"""Credential handling: error classification, and the no-persistence guarantee.

Nothing here touches the network -- these cover the decision logic that routes
a failure to the sign-in prompt, and the promise that a password never reaches
a profile or a report.
"""

from __future__ import annotations

import json
import unittest

from nassync.config import Profile, SyncOptions
from nassync.credentials import AuthenticationError, sign_in
from nassync.model import SharePair
from nassync.shares import ShareEnumerationError
from nassync.winerror import error_text, is_auth_error


class AuthErrorClassificationTestCase(unittest.TestCase):
    """Only failures a password could fix should offer a sign-in form."""

    def test_credential_failures_are_recognised(self):
        for code in (
            5,     # access denied
            86,    # invalid password
            1219,  # session credential conflict
            1326,  # logon failure
            1331,  # account disabled
            2202,  # bad user name
        ):
            self.assertTrue(is_auth_error(code), f"{code} should ask for credentials")

    def test_connectivity_failures_are_not_credential_failures(self):
        for code in (53, 67, 1202, 2114):
            self.assertFalse(
                is_auth_error(code), f"{code} must not ask for credentials"
            )

    def test_error_text_is_readable_and_never_empty(self):
        self.assertIn("user name or password", error_text(1326))
        self.assertIn("network path not found", error_text(53))
        self.assertTrue(error_text(999999).strip())

    def test_share_error_defaults_to_not_needing_credentials(self):
        self.assertFalse(ShareEnumerationError("gone").needs_credentials)

    def test_share_error_carries_the_credential_flag(self):
        error = ShareEnumerationError("denied", 1326, needs_credentials=True)
        self.assertTrue(error.needs_credentials)
        self.assertEqual(error.code, 1326)


class SignInGuardTestCase(unittest.TestCase):
    """Input validation happens before anything touches the network."""

    def test_missing_username_is_rejected(self):
        with self.assertRaises(AuthenticationError) as caught:
            sign_in("SERVER", "", "secret")
        self.assertTrue(caught.exception.needs_credentials)

    def test_missing_server_is_not_a_credential_problem(self):
        with self.assertRaises(AuthenticationError) as caught:
            sign_in("", "user", "secret")
        self.assertFalse(caught.exception.needs_credentials)


class PasswordPersistenceTestCase(unittest.TestCase):
    """A password must never survive into anything written to disk."""

    def test_profile_has_no_field_capable_of_holding_a_password(self):
        profile = Profile(
            name="p",
            source_server="OLDSERVER",
            target_server="NEWNAS",
            pairs=[SharePair("OLDSERVER", "Data", "NEWNAS", "Data")],
            options=SyncOptions(),
        )
        serialised = json.dumps(profile.to_dict()).lower()
        for forbidden in ("password", "passwd", "secret", "credential"):
            self.assertNotIn(forbidden, serialised)

    def test_profile_round_trip_is_lossless_without_credentials(self):
        profile = Profile(name="p", source_server="A", target_server="B")
        restored = Profile.from_dict(profile.to_dict())
        self.assertEqual(restored.source_server, "A")
        self.assertEqual(restored.target_server, "B")


if __name__ == "__main__":
    unittest.main()
