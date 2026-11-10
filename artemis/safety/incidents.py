"""Canonical cross-layer incident names (contract §7.2).

Internal Validator categories are intentionally richer than the cross-layer
vocabulary. Convert them at the boundary; callers outside L3/L4 must only ever
see the values in :data:`CANONICAL_INCIDENTS`.
"""

from __future__ import annotations

__all__ = ["CANONICAL_INCIDENTS", "canonical_incident"]

CANONICAL_INCIDENTS = frozenset(
    {
        "capability_unavailable",
        "command_unsupported",
        "device_disconnected",
        "transport_error",
        "firmware_error",
        "resource_busy",
        "capture_timeout",
        "uvc_no_signal",
        "decode_error",
        "stale_frame",
        "coordinate_space_mismatch",
        "target_disappeared",
        "target_shifted",
        "target_occupied",
        "target_unverified",
        "pointer_missing",
        "pointer_off_target",
        "pointer_not_centered",
        "screen_changed",
        "no_effect",
        "action_rejected",
        "verification_failed",
        "cancelled",
        "deadline_exceeded",
        "safety_interlock",
    }
)

_INTERNAL = {
    "none": None,
    "pixel_target_disappeared": "target_disappeared",
    "pixel_bypassed": "verification_failed",
    "xml_bypassed": "verification_failed",
    "general": "verification_failed",
}


def canonical_incident(value: object) -> str | None:
    """Map a category/incident to the canonical table (or None for no incident)."""

    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    if not text:
        return None
    if text in _INTERNAL:
        return _INTERNAL[text]
    return text if text in CANONICAL_INCIDENTS else None
