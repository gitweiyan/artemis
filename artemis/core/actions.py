"""Typed action intent/command/receipt/verification (ADR-004).

``ActionResult.ok`` remains the compatibility signal for "the adapter accepted
or dispatched the command". The DTOs here keep that fact separate from the
observed screen effect and from Checker/Validator acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Mapping

from artemis.core.coordinates import BoxRef, PointRef

__all__ = [
    "ActionCommand",
    "ActionIntent",
    "ActionReceipt",
    "ActionState",
    "Idempotency",
    "VerificationResult",
    "can_retry",
    "state_from_result",
    "transition_allowed",
]


class ActionState(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    DISPATCHED = "dispatched"
    ADAPTER_ACKNOWLEDGED = "adapter_acknowledged"
    OBSERVED_EFFECT = "observed_effect"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


class Idempotency(StrEnum):
    SAFE = "safe"
    CONDITIONAL = "conditional"
    UNSAFE = "unsafe"


_TERMINAL = {
    ActionState.VERIFIED_SUCCESS,
    ActionState.VERIFIED_FAILURE,
    ActionState.REJECTED,
    ActionState.CANCELLED,
    ActionState.DEADLINE_EXCEEDED,
}

_ALLOWED: dict[ActionState, set[ActionState]] = {
    ActionState.PROPOSED: {ActionState.VALIDATED, ActionState.REJECTED, ActionState.CANCELLED},
    ActionState.VALIDATED: {ActionState.DISPATCHED, ActionState.REJECTED, ActionState.CANCELLED},
    ActionState.DISPATCHED: {
        ActionState.ADAPTER_ACKNOWLEDGED,
        ActionState.UNKNOWN,
        ActionState.CANCELLED,
        ActionState.DEADLINE_EXCEEDED,
    },
    ActionState.ADAPTER_ACKNOWLEDGED: {
        ActionState.OBSERVED_EFFECT,
        ActionState.UNKNOWN,
        ActionState.CANCELLED,
    },
    ActionState.OBSERVED_EFFECT: {
        ActionState.VERIFIED_SUCCESS,
        ActionState.VERIFIED_FAILURE,
        ActionState.UNKNOWN,
    },
    ActionState.UNKNOWN: {
        ActionState.OBSERVED_EFFECT,
        ActionState.VERIFIED_SUCCESS,
        ActionState.VERIFIED_FAILURE,
        ActionState.CANCELLED,
    },
    ActionState.VERIFIED_SUCCESS: set(),
    ActionState.VERIFIED_FAILURE: set(),
    ActionState.REJECTED: set(),
    ActionState.CANCELLED: set(),
    ActionState.DEADLINE_EXCEEDED: set(),
}


def _freeze(values: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(values or {}))


def _text(value: object, *, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _target(value: PointRef | BoxRef | None) -> PointRef | BoxRef | None:
    if value is not None and not isinstance(value, (PointRef, BoxRef)):
        raise ValueError("target must be a PointRef, BoxRef or None")
    return value


@dataclass(frozen=True)
class ActionIntent:
    intent_id: str
    kind: str
    target: PointRef | BoxRef | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    idempotency: Idempotency = Idempotency.UNSAFE
    risk: str = "normal"

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _text(self.intent_id, name="intent_id"))
        object.__setattr__(self, "kind", _text(self.kind, name="kind"))
        object.__setattr__(self, "target", _target(self.target))
        object.__setattr__(self, "parameters", _freeze(self.parameters))
        object.__setattr__(self, "idempotency", Idempotency(self.idempotency))
        object.__setattr__(self, "risk", _text(self.risk, name="risk"))


@dataclass(frozen=True)
class ActionCommand:
    command_id: str
    intent_id: str
    attempt: int
    adapter_id: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    target_ref: PointRef | BoxRef | None = None
    frame_id: str | None = None
    transform_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _text(self.command_id, name="command_id"))
        object.__setattr__(self, "intent_id", _text(self.intent_id, name="intent_id"))
        if int(self.attempt) < 1:
            raise ValueError("attempt must be >= 1")
        object.__setattr__(self, "attempt", int(self.attempt))
        object.__setattr__(self, "adapter_id", _text(self.adapter_id, name="adapter_id"))
        object.__setattr__(self, "parameters", _freeze(self.parameters))
        object.__setattr__(self, "target_ref", _target(self.target_ref))


@dataclass(frozen=True)
class ActionReceipt:
    action_id: str
    intent_id: str
    command_id: str
    attempt: int
    adapter_id: str
    state: ActionState
    dispatched_at: float
    acknowledged_at: float | None = None
    incident: str | None = None
    metrics: Mapping[str, float | str] = field(default_factory=dict)
    retry_of: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "intent_id": self.intent_id,
            "command_id": self.command_id,
            "attempt": self.attempt,
            "adapter_id": self.adapter_id,
            "state": self.state.value,
            "dispatched_at": self.dispatched_at,
            "acknowledged_at": self.acknowledged_at,
            "incident": self.incident,
            "metrics": dict(self.metrics),
            "retry_of": self.retry_of,
            "evidence_refs": list(self.evidence_refs),
        }

    def __post_init__(self) -> None:
        for name in ("action_id", "intent_id", "command_id", "adapter_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name=name))
        if int(self.attempt) < 1:
            raise ValueError("attempt must be >= 1")
        object.__setattr__(self, "attempt", int(self.attempt))
        state = ActionState(self.state)
        if state not in {
            ActionState.DISPATCHED,
            ActionState.ADAPTER_ACKNOWLEDGED,
            ActionState.OBSERVED_EFFECT,
            ActionState.REJECTED,
            ActionState.CANCELLED,
            ActionState.DEADLINE_EXCEEDED,
            ActionState.UNKNOWN,
        }:
            raise ValueError(f"receipt cannot carry state {state.value!r}")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "metrics", _freeze(self.metrics))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True)
class VerificationResult:
    action_id: str
    status: Literal["success", "failure", "unknown"]
    observed_at: float
    reason: str | None = None
    evidence_refs: tuple[str, ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", _text(self.action_id, name="action_id"))
        if self.status not in {"success", "failure", "unknown"}:
            raise ValueError(f"unknown verification status {self.status!r}")
        if self.status == "success" and not self.evidence_refs:
            raise ValueError("verification success requires at least one evidence reference")
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be within 0..1")
        object.__setattr__(self, "evidence_refs", tuple(str(ref) for ref in self.evidence_refs))


def can_retry(
    idempotency: Idempotency | str,
    *,
    observed_no_effect: bool = False,
    transport_failure: bool = False,
) -> bool:
    """ADR-004 D3: only declared-safe actions may be retried automatically."""

    policy = Idempotency(idempotency)
    if policy is Idempotency.SAFE:
        return True
    if policy is Idempotency.CONDITIONAL:
        return bool(observed_no_effect or transport_failure)
    return False


def state_from_result(ok: bool, incident: str | None = None) -> ActionState:
    """Map a legacy ``ActionResult`` to the furthest state it can prove."""

    if ok:
        return ActionState.ADAPTER_ACKNOWLEDGED
    if incident in {"capability_unavailable", "coordinate_space_mismatch", "action_rejected"}:
        return ActionState.REJECTED
    return ActionState.UNKNOWN


def transition_allowed(current: ActionState | str, target: ActionState | str) -> bool:
    return ActionState(target) in _ALLOWED[ActionState(current)]
