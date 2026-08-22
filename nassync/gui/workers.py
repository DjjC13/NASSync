"""Background workers.

Qt widgets may only be touched from the GUI thread, so every worker here does
its work in a :class:`QThread` and communicates purely through signals. Each
worker owns a :class:`threading.Event` for cancellation, which the engine polls
at safe points.
"""

from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, QThread, Signal

from ..config import Profile
from ..credentials import AuthenticationError, sign_in
from ..exclusions import ExclusionSet
from ..executor import ExecProgress, Executor, RunResult
from ..journal import RunJournal
from ..model import Plan
from ..planner import PlanProgress, build_plan
from ..scanner import ScanCancelled
from ..shares import ShareEnumerationError, list_shares
from ..verify import VerificationResult, verify


class _Worker(QThread):
    """Common error plumbing: never let an exception die silently in a thread."""

    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:  # pragma: no cover - Qt entry point
        try:
            self.work()
        except ScanCancelled:
            pass
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")

    def work(self) -> None:
        raise NotImplementedError


class ShareWorker(_Worker):
    """Enumerates shares on one server, optionally signing in first."""

    listed = Signal(str, list)  # role ("source"/"target"), [ShareInfo]
    auth_required = Signal(str, str)  # role, explanation

    def __init__(self, role: str, server: str, username: str = "", password: str = "",
                 parent=None):
        super().__init__(parent)
        self.role = role
        self.server = server
        self.username = username
        self._password = password

    def work(self) -> None:
        try:
            if self.username:
                sign_in(self.server, self.username, self._password)
        except AuthenticationError as exc:
            self._report(exc)
            return
        finally:
            # Held no longer than the Win32 call needs it.
            self._password = ""

        try:
            self.listed.emit(self.role, list_shares(self.server))
        except ShareEnumerationError as exc:
            self._report(exc)

    def _report(self, exc) -> None:
        """Route a failure to the sign-in prompt or the general error banner."""
        if getattr(exc, "needs_credentials", False):
            self.auth_required.emit(self.role, str(exc))
        else:
            self.failed.emit(str(exc))


class ScanWorker(_Worker):
    """Scans both trees for every enabled pair and builds the plan."""

    progressed = Signal(object)  # PlanProgress
    finished_plan = Signal(object)  # Plan

    def __init__(self, profile: Profile, parent=None):
        super().__init__(parent)
        self.profile = profile

    def work(self) -> None:
        plan: Plan = build_plan(
            self.profile.enabled_pairs,
            ExclusionSet(self.profile.exclusions),
            self.profile.options,
            cancel=self.cancel_event,
            progress=self.progressed.emit,
        )
        if not self.cancel_event.is_set():
            self.finished_plan.emit(plan)


class ExecuteWorker(_Worker):
    """Runs the approved plan, then optionally verifies it."""

    progressed = Signal(object)  # ExecProgress
    logged = Signal(str)
    verify_started = Signal()
    verify_progressed = Signal(object)  # PlanProgress
    finished_run = Signal(object, object)  # RunResult, VerificationResult | None

    def __init__(self, profile: Profile, plan: Plan, journal: RunJournal, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.plan = plan
        self.journal = journal
        self.executor: Executor | None = None

    def work(self) -> None:
        self.executor = Executor(
            self.profile,
            self.plan,
            journal=self.journal,
            on_progress=self.progressed.emit,
            on_log=self.logged.emit,
            run_id=self.journal.run_id,
        )
        result: RunResult = self.executor.run()

        verification: VerificationResult | None = None
        if (
            self.profile.options.verify_after_run
            and not result.cancelled
            and not self.cancel_event.is_set()
        ):
            self.logged.emit("Verifying that the target now mirrors the source...")
            self.verify_started.emit()
            # Verification re-reads both trees in full, which on a large share
            # takes minutes -- without progress it is indistinguishable from a
            # hang, so the same PlanProgress events the scan page uses are
            # forwarded here too.
            verification = verify(
                self.profile,
                cancel=self.cancel_event,
                progress=self.verify_progressed.emit,
            )
            self.logged.emit(verification.headline)

        self.finished_run.emit(result, verification)

    def cancel(self) -> None:
        super().cancel()
        if self.executor is not None:
            self.executor.cancel.set()

    def set_paused(self, paused: bool) -> None:
        if self.executor is None:
            return
        if paused:
            self.executor.paused.set()
        else:
            self.executor.paused.clear()


class VerifyWorker(_Worker):
    """Standalone verification pass, for re-checking a finished migration."""

    progressed = Signal(object)  # PlanProgress
    finished_verify = Signal(object)  # VerificationResult

    def __init__(self, profile: Profile, parent=None):
        super().__init__(parent)
        self.profile = profile

    def work(self) -> None:
        result = verify(
            self.profile, cancel=self.cancel_event, progress=self.progressed.emit
        )
        if not self.cancel_event.is_set():
            self.finished_verify.emit(result)
