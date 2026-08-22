"""Post-run verification: rescan both sides and prove the mirror is complete.

This is the evidence for a cutover decision, so it is deliberately independent
of what the run *believed* it did -- it re-reads both trees from scratch and
compares them again.

Two categories come out of it:

* **Differences** -- copies, overwrites or deletions still outstanding. Any of
  these means the mirror is not finished, and verification fails.
* **Accepted differences** -- conflicts the operator chose to keep, and names
  that cannot exist on Windows. These are expected, reported, and do not fail
  verification, because a human already decided about each one.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .config import Profile
from .exclusions import ExclusionSet
from .model import Action, Plan, PlanItem
from .planner import build_plan

#: Anything in these categories means the mirror is genuinely incomplete.
_FAILING_ACTIONS = (
    Action.COPY,
    Action.OVERWRITE,
    Action.MKDIR,
    Action.DELETE,
    Action.DELETE_DIR,
)


@dataclass
class VerificationResult:
    """Outcome of a verification pass."""

    differences: list[PlanItem] = field(default_factory=list)
    accepted: list[PlanItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    identical: int = 0

    @property
    def passed(self) -> bool:
        return not self.differences and not self.errors

    @property
    def headline(self) -> str:
        if self.passed and not self.accepted:
            return "Verified: the target is an exact mirror of the source."
        if self.passed:
            return (
                f"Verified: mirror complete, with {len(self.accepted)} "
                "accepted difference(s) you decided about."
            )
        if self.errors and not self.differences:
            return (
                f"Verification incomplete: {len(self.errors)} path(s) could not be read."
            )
        return f"Verification FAILED: {len(self.differences)} difference(s) remain."


def verify(
    profile: Profile,
    cancel: threading.Event | None = None,
    progress=None,
) -> VerificationResult:
    """Rescan every enabled share pair and report what still differs."""
    plan: Plan = build_plan(
        profile.enabled_pairs,
        ExclusionSet(profile.exclusions),
        profile.options,
        cancel=cancel,
        progress=progress,
    )
    result = VerificationResult(identical=plan.identical, errors=list(plan.errors))
    for item in plan.items:
        if item.action in _FAILING_ACTIONS:
            result.differences.append(item)
        else:
            result.accepted.append(item)
    return result
