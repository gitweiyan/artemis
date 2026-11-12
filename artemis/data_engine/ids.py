"""Correlation IDs and artifact/action evidence DTOs (Phase 6).

Data Engine owns Task/Step/Action facts, Telemetry owns spans, and the Runtime
status store owns background task status. These IDs are the only join keys
between them; an image is represented by an ArtifactRef, never copied into
multiple JSON payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = ["ActionEvidence", "ArtifactRef", "DataIds"]


def _id(value: object, *, name: str, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    text = str(value).strip()
    if not text:
        if required:
            raise ValueError(f"{name} must not be empty")
        return None
    return text


@dataclass(frozen=True)
class DataIds:
    """Join keys for one action/observation record."""

    session_id: str
    device_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None
    frame_id: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _id(self.session_id, name="session_id", required=True)
        )
        for name in ("device_id", "task_id", "step_id", "action_id", "frame_id", "trace_id"):
            object.__setattr__(self, name, _id(getattr(self, name), name=name))

    def as_dict(self) -> dict[str, str | None]:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "action_id": self.action_id,
            "frame_id": self.frame_id,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class ArtifactRef:
    """A stored artifact addressed by hash; never inline the bytes in a trace."""

    artifact_id: str
    kind: str
    content_type: str | None = None
    size_bytes: int | None = None
    uri: str | None = None

    def __post_init__(self) -> None:
        artifact_id = _id(self.artifact_id, name="artifact_id", required=True)
        kind = _id(self.kind, name="kind", required=True)
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "kind", kind)
        if self.size_bytes is not None and int(self.size_bytes) < 0:
            raise ValueError("size_bytes must not be negative")
        if self.size_bytes is not None:
            object.__setattr__(self, "size_bytes", int(self.size_bytes))

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "uri": self.uri,
        }


@dataclass(frozen=True)
class ActionEvidence:
    """Structured facts needed to replay/explain one action."""

    ids: DataIds
    profile_id: str | None = None
    adapter_id: str | None = None
    source: str | None = None
    target: Mapping[str, Any] = field(default_factory=dict)
    coordinate_space: str | None = None
    capability_decision: Mapping[str, Any] = field(default_factory=dict)
    dispatch_result: Mapping[str, Any] = field(default_factory=dict)
    device_ack: str | None = None
    #: Furthest proven action state: ack alone must never become verified.
    action_state: str | None = None
    #: Production dispatch/ack/effect receipt (serialized ActionReceipt).
    action_receipt: Mapping[str, Any] = field(default_factory=dict)
    #: Task-level verification owned by Checker/acceptance, not an adapter ACK.
    goal_verified: bool = False
    pre_verification: Mapping[str, Any] = field(default_factory=dict)
    post_verification: Mapping[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None
    incident: str | None = None
    artifacts: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.ids, DataIds):
            raise TypeError("ids must be DataIds")
        for name in (
            "target",
            "capability_decision",
            "dispatch_result",
            "pre_verification",
            "post_verification",
        ):
            object.__setattr__(self, name, dict(getattr(self, name) or {}))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if self.latency_ms is not None and float(self.latency_ms) < 0:
            raise ValueError("latency_ms must not be negative")
        if self.latency_ms is not None:
            object.__setattr__(self, "latency_ms", float(self.latency_ms))

    def as_dict(self) -> dict[str, Any]:
        return {
            "ids": self.ids.as_dict(),
            "profile_id": self.profile_id,
            "adapter_id": self.adapter_id,
            "source": self.source,
            "target": dict(self.target),
            "coordinate_space": self.coordinate_space,
            "capability_decision": dict(self.capability_decision),
            "dispatch_result": dict(self.dispatch_result),
            "device_ack": self.device_ack,
            "action_state": self.action_state,
            "action_receipt": dict(self.action_receipt),
            "goal_verified": bool(self.goal_verified),
            "pre_verification": dict(self.pre_verification),
            "post_verification": dict(self.post_verification),
            "latency_ms": self.latency_ms,
            "incident": self.incident,
            "artifacts": [item.as_dict() for item in self.artifacts],
        }
