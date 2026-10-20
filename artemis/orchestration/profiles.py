"""Which execution profile runs this task, and how deep its perception goes.

The profile (``flash`` / ``pro`` / ``skill``) and the Explorer tier are *orchestration*
choices: they decide the shape of a run before a single frame is captured. Keeping the
choice here — instead of in the CLI flags plus the config resolver plus each caller —
means it can be tested, logged with a reason, and later driven by task signals
(latency budget, risk, novelty) as the target architecture describes.

Tier precedence itself is **not** re-implemented: it stays in ``ExplorerConfig.resolve``
(one rule, one place). This module decides the profile and asks the config for the tier
that belongs to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["PROFILE_KINDS", "ProfilePlan", "ProfileSelectionError", "select_profile"]

#: ``flash`` = single reactive loop; ``pro`` = Planner/Operator/Validator graph;
#: ``skill`` = a fixed closed loop that bypasses per-frame model calls.
PROFILE_KINDS = ("flash", "pro", "skill")
#: Used when the request does not name a profile. Matches the CLI default.
DEFAULT_PROFILE = "pro"


class ProfileSelectionError(ValueError):
    """The requested profile is not one this runtime can run."""


@dataclass(frozen=True)
class ProfilePlan:
    """The execution profile for one task, its Explorer tier, and why."""

    kind: str
    explorer_tier: str
    reason: str
    requested: bool = True


def _normalize(requested: str | None) -> tuple[str, bool]:
    if requested is None or not str(requested).strip():
        return DEFAULT_PROFILE, False
    kind = str(requested).strip().lower()
    if kind not in PROFILE_KINDS:
        raise ProfileSelectionError(
            f"unknown execution profile {requested!r}; expected one of {', '.join(PROFILE_KINDS)}"
        )
    return kind, True


def select_profile(
    requested: str | None,
    *,
    explorer: Any = None,
    explicit_tier: str | None = None,
) -> ProfilePlan:
    """The plan for ``requested`` (``None`` → the default), with its Explorer tier.

    ``explorer`` is an ``ExplorerConfig``-shaped object (``resolve(profile=...)``);
    omitting it falls back to the shipped defaults, which is what a headless test or a
    script wants.
    """
    kind, was_requested = _normalize(requested)
    if explorer is None:
        from artemis.config.agent import ExplorerConfig

        explorer = ExplorerConfig()

    tier = explorer.resolve(explicit_version=explicit_tier, profile=kind)
    if was_requested:
        reason = f"the task asked for the {kind} profile"
    else:
        reason = f"no profile was requested, so the {DEFAULT_PROFILE} graph runs"
    if explicit_tier:
        reason += f"; the Explorer tier was pinned to {tier}"

    return ProfilePlan(
        kind=kind,
        explorer_tier=str(tier),
        reason=reason,
        requested=was_requested,
    )
