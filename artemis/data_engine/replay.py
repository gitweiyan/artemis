"""Read-only replay/query helpers for action evidence (Phase 6).

Data Engine remains the owner of the facts; these helpers only select records
that were written into action traces. They never re-run a device action.
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = ["query_action_evidence"]


def _evidence(trace: Any) -> dict[str, Any] | None:
    payload = getattr(trace, "payload", None)
    if isinstance(payload, dict):
        value = payload.get("action_evidence")
        if isinstance(value, dict):
            return value
    extra = getattr(trace, "extra_metadata", None)
    if isinstance(extra, dict):
        value = extra.get("action_evidence")
        if isinstance(value, dict):
            return value
    return None


def query_action_evidence(
    traces: Iterable[Any],
    *,
    action_id: str | None = None,
    session_id: str | None = None,
    device_id: str | None = None,
    artifact_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Select action evidence by ID/device/artifact; newest traces last."""

    selected: list[dict[str, Any]] = []
    for trace in traces:
        evidence = _evidence(trace)
        if evidence is None:
            continue
        ids = evidence.get("ids") or {}
        if action_id is not None and str(ids.get("action_id")) != str(action_id):
            continue
        if session_id is not None and str(ids.get("session_id")) != str(session_id):
            continue
        if device_id is not None and str(ids.get("device_id")) != str(device_id):
            continue
        if artifact_id is not None:
            artifacts = evidence.get("artifacts") or []
            if not any(str(item.get("artifact_id")) == str(artifact_id) for item in artifacts):
                continue
        selected.append(evidence)
        if len(selected) >= max(1, int(limit)):
            break
    return selected
