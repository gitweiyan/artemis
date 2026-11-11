"""Flash action-risk policy (Stage 4).

Flash is a reactive, low-latency loop: it must not inherit the Pro XML/pixel gate
wholesale. This table makes the risk posture explicit per action so traces can
show whether a dispatch was a single verified action or a burst member.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["FlashActionPolicy", "FlashMode", "flash_action_policy"]


class FlashMode(StrEnum):
    SINGLE = "single"
    BURST = "burst"


@dataclass(frozen=True)
class FlashActionPolicy:
    action: str
    mode: FlashMode
    requires_precondition: bool
    requires_post_verification: bool
    pointer_precheck: bool
    burst_allowed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "mode": self.mode.value,
            "requires_precondition": self.requires_precondition,
            "requires_post_verification": self.requires_post_verification,
            "pointer_precheck": self.pointer_precheck,
            "burst_allowed": self.burst_allowed,
        }


_SINGLE_RISK = frozenset({"click", "tap", "long_press", "input_text", "focus_and_input_text"})
_POST_VERIFY = frozenset()


def flash_action_policy(action: str, *, burst: bool = False) -> FlashActionPolicy:
    """The declared safety posture; it is evidence, not a claim of verification."""

    name = str(action or "").strip()
    mode = FlashMode.BURST if burst else FlashMode.SINGLE
    return FlashActionPolicy(
        action=name,
        mode=mode,
        requires_precondition=name in _SINGLE_RISK and not burst,
        # Flash is latency-first: pixel verification is opt-in per action.
        requires_post_verification=name in _POST_VERIFY,
        pointer_precheck=name in {"click", "tap", "long_press"},
        burst_allowed=name not in _SINGLE_RISK,
    )
