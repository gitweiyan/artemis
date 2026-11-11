"""Flash action-risk policy (Stage 4).

Flash is a reactive, low-latency loop: it must not inherit the Pro XML/pixel gate
wholesale. This table makes the risk posture explicit per action so traces can
show whether a dispatch was a single verified action or a burst member.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "FlashActionPolicy",
    "FlashMode",
    "FLASH_BURST_ACTIONS",
    "FlashBurstBudget",
    "FlashPrecondition",
    "MAX_FLASH_ACTIONS_PER_TURN",
    "evaluate_flash_precondition",
    "flash_action_policy",
    "flash_burst_budget",
]


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


@dataclass(frozen=True)
class FlashPrecondition:
    """The cheap precondition verdict for one Flash action."""

    allowed: bool
    incident: str | None = None
    reason: str = ""
    observation_skew_s: float | None = None
    pointer_precheck: str = "not_required"

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "incident": self.incident,
            "reason": self.reason,
            "observation_skew_s": self.observation_skew_s,
            "pointer_precheck": self.pointer_precheck,
        }


def evaluate_flash_precondition(
    action: str,
    args: dict,
    *,
    state=None,
    index_elements=None,
    pre_screenshot: bytes | None,
    ctx=None,
) -> FlashPrecondition:
    """Validate cheap, available evidence without importing the Pro gate.

    Pointer alignment itself remains the controller's responsibility; this check
    only verifies that the action has a usable observation, a valid target shape,
    and an existing indexed element. It is deliberately latency-free.
    """

    name = str(action or "").strip()
    policy = flash_action_policy(name)
    if not policy.requires_precondition:
        return FlashPrecondition(True)

    if not pre_screenshot:
        return FlashPrecondition(False, "stale_frame", "no pre-action observation")

    target = args.get("target") if isinstance(args, dict) else None
    if target is None and isinstance(args, dict):
        target = args.get("coordinates")
    if isinstance(target, int) or (isinstance(target, str) and target.strip().isdigit()):
        index = int(target)
        elements = index_elements
        if elements is None and state is not None:
            elements = getattr(state, "indexed_elements", None)
        if isinstance(elements, (list, tuple)) and (index < 1 or index > len(elements)):
            return FlashPrecondition(False, "target_disappeared", f"Invalid target index {index}")
    elif isinstance(target, (list, tuple)):
        if len(target) != 2:
            return FlashPrecondition(False, "coordinate_space_mismatch", "target is not a point")
        try:
            x, y = int(target[0]), int(target[1])
        except (TypeError, ValueError):
            return FlashPrecondition(False, "coordinate_space_mismatch", "target point is invalid")
        if not (0 <= x <= 1000 and 0 <= y <= 1000):
            return FlashPrecondition(
                False, "coordinate_space_mismatch", f"target {x},{y} is outside 0..1000"
            )

    pointer_precheck = "not_required"
    session = getattr(ctx, "device_session", None) if ctx is not None else None
    capabilities = getattr(session, "capabilities", None) if session is not None else None
    if getattr(capabilities, "requires_pointer_precheck", False):
        sinks = getattr(session, "control", []) or []
        if not any(
            getattr(getattr(sink, "capabilities", None), "supports_relative_pointer", False)
            for sink in sinks
        ):
            return FlashPrecondition(
                False,
                "capability_unavailable",
                "relative pointer sink is missing; cannot satisfy pointer precheck",
            )
        # Alignment is performed by the relative-pointer sink immediately before
        # the click; Flash does not duplicate that loop (latency-first).
        pointer_precheck = "delegated"

    skew = None
    if ctx is not None:
        from artemis.runtime import observation_clock

        skew = observation_clock.skew_s(ctx)
    return FlashPrecondition(True, observation_skew_s=skew, pointer_precheck=pointer_precheck)


#: A Flash turn is still a bounded batch, not an unbounded model-controlled loop.
MAX_FLASH_ACTIONS_PER_TURN = 8

#: Actions that may appear more than once in a sequential Flash turn. Flash
#: observes after each action, so this is a budgeted sequence, not Pro's unvetted
#: fast-action burst.
FLASH_BURST_ACTIONS = frozenset(
    {
        "click",
        "click_sequence",
        "long_press",
        "input_text",
        "focus_and_input_text",
        "swipe",
        "press_key",
        "manage_app",
        "open_link",
        "wait_for_delay",
        "wait_for_text",
        "erase_one_char",
        "clear_text",
        "focus_and_clear_text",
    }
)


@dataclass(frozen=True)
class FlashBurstBudget:
    max_actions: int = MAX_FLASH_ACTIONS_PER_TURN
    allowed_actions: frozenset[str] = FLASH_BURST_ACTIONS

    def allows(self, action: str, position: int) -> bool:
        """Whether the ``position``-th device action may run in this turn."""

        return str(action) in self.allowed_actions and 1 <= int(position) <= max(
            1, int(self.max_actions)
        )


def flash_burst_budget() -> FlashBurstBudget:
    return FlashBurstBudget()
