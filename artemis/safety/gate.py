"""Task-mode safety gate port (ADR-007 Phase 5).

Task-mode actions (Flash/Pro) may pay for a slower precondition/postcondition
check; Reflex uses its own bounded budget instead. This module defines the
shared vocabulary without implementing the Validator's XML/pixel policy here:
the Validator adapts its existing checks into an :class:`ActionGate`.

Incident values in decisions are canonical §7.2 strings. Internal Validator
categories (``pixel_bypassed`` etc.) must be translated at this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol, runtime_checkable

__all__ = [
    "ActionGate",
    "GateDecision",
    "SafetyMode",
    "SafetyPolicy",
    "SafetyRequest",
]


class SafetyMode(StrEnum):
    TASK = "task"
    REFLEX = "reflex"


@dataclass(frozen=True)
class SafetyRequest:
    """What the gate needs to decide, independent of a particular platform."""

    action: str
    mode: SafetyMode = SafetyMode.TASK
    target: Any = None
    frame_age_s: float | None = None
    confidence: float | None = None
    capability: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.action or "").strip():
            raise ValueError("action must not be empty")
        object.__setattr__(self, "mode", SafetyMode(self.mode))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if self.confidence is not None and not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("confidence must be in 0..1 when provided")


@dataclass(frozen=True)
class GateDecision:
    """A gate verdict; ``incident`` is canonical §7.2 (never an internal alias)."""

    allowed: bool
    incident: str | None = None
    reason: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    requires_post_verification: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", dict(self.evidence or {}))
        if not self.allowed and not self.incident:
            raise ValueError("a blocked decision needs a canonical incident")

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "incident": self.incident,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "requires_post_verification": self.requires_post_verification,
        }


@runtime_checkable
class ActionGate(Protocol):
    """Pre/post checks for one task-mode action."""

    async def check(self, request: SafetyRequest) -> GateDecision: ...

    async def verify(self, request: SafetyRequest, result: Any) -> GateDecision: ...


@dataclass(frozen=True)
class SafetyPolicy:
    """A named gate plus the execution mode it is allowed to serve."""

    policy_id: str
    mode: SafetyMode
    gate: ActionGate

    def __post_init__(self) -> None:
        if not str(self.policy_id or "").strip():
            raise ValueError("policy_id must not be empty")
        object.__setattr__(self, "policy_id", str(self.policy_id))
        object.__setattr__(self, "mode", SafetyMode(self.mode))
