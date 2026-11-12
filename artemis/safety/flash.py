"""Flash action-risk policy (Stage 4).

Flash is a reactive, low-latency loop: it must not inherit the Pro XML/pixel gate
wholesale. This table makes the risk posture explicit per action so traces can
show whether a dispatch was a single verified action or a burst member.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "FlashActionCost",
    "FlashActionPolicy",
    "FlashMode",
    "FLASH_BURST_ACTIONS",
    "FlashBurstBudget",
    "FLASH_REQUIRED_EFFECT_ACTIONS",
    "MAX_FLASH_CLICKS_PER_TURN",
    "MAX_FLASH_RISK_UNITS",
    "FlashPrecondition",
    "FLASH_HIGH_RISK_ACTIONS",
    "MAX_FLASH_ACTIONS_PER_TURN",
    "MAX_FLASH_HIGH_RISK_ACTIONS_PER_TURN",
    "evaluate_flash_precondition",
    "flash_action_cost",
    "flash_action_policy",
    "flash_burst_budget",
]


class FlashMode(StrEnum):
    SINGLE = "single"
    BURST = "burst"


@dataclass(frozen=True)
class FlashActionCost:
    """Actual cost of one tool call, independent of its name."""

    tool_actions: int = 1
    clicks: int = 0
    risk_units: int = 1


def flash_action_cost(action: str, args: dict | None = None) -> FlashActionCost:
    """Count real clicks and risk units, not just the number of tool calls."""

    name = str(action or "").strip()
    payload = args if isinstance(args, dict) else {}
    if name == "click":
        try:
            clicks = max(1, int(payload.get("times", 1)))
        except (TypeError, ValueError):
            clicks = 1
        return FlashActionCost(clicks=clicks, risk_units=2 * clicks)
    if name == "click_sequence":
        sequence = payload.get("sequence")
        clicks = len(sequence) if isinstance(sequence, (list, tuple)) else 0
        return FlashActionCost(clicks=clicks, risk_units=2 * clicks)
    if name in {"long_press", "input_text", "focus_and_input_text"}:
        return FlashActionCost(clicks=1, risk_units=2)
    return FlashActionCost()


@dataclass(frozen=True)
class FlashActionPolicy:
    action: str
    mode: FlashMode
    requires_precondition: bool
    requires_post_verification: bool
    pointer_precheck: bool
    burst_allowed: bool
    high_risk: bool = False
    max_observation_age_s: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "mode": self.mode.value,
            "requires_precondition": self.requires_precondition,
            "requires_post_verification": self.requires_post_verification,
            "pointer_precheck": self.pointer_precheck,
            "burst_allowed": self.burst_allowed,
            "high_risk": self.high_risk,
            "max_observation_age_s": self.max_observation_age_s,
        }


_SINGLE_RISK = frozenset(
    {"click", "tap", "long_press", "input_text", "focus_and_input_text", "click_sequence"}
)
#: Actions that can have an irreversible or hard-to-recover effect. They remain
#: admissible in a burst, but only up to a small explicit high-risk budget.
FLASH_HIGH_RISK_ACTIONS = _SINGLE_RISK
#: High-value actions must leave observable effect evidence (dHash or explicit pixel).
FLASH_REQUIRED_EFFECT_ACTIONS = frozenset(
    {"click", "click_sequence", "manage_app", "open_link"}
)
_POST_VERIFY = FLASH_REQUIRED_EFFECT_ACTIONS

#: A single high-risk action is planned from a frame that must still be recent.
#: The turn's LLM call already costs seconds; 5s keeps normal Flash turns usable
#: while refusing a frame that predates a splash/loading-screen transition.
FLASH_MAX_OBSERVATION_AGE_S = 5.0


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
        # Use the same authoritative allowlist as FlashBurstBudget; the policy
        # and the dispatch gate must not disagree about what may enter a burst.
        burst_allowed=name in FLASH_BURST_ACTIONS,
        high_risk=name in FLASH_HIGH_RISK_ACTIONS,
        max_observation_age_s=(FLASH_MAX_OBSERVATION_AGE_S if name in _SINGLE_RISK else None),
    )


@dataclass(frozen=True)
class FlashPrecondition:
    """The cheap precondition verdict for one Flash action."""

    allowed: bool
    incident: str | None = None
    reason: str = ""
    observation_skew_s: float | None = None
    max_observation_age_s: float | None = None
    pointer_precheck: str = "not_required"

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "incident": self.incident,
            "reason": self.reason,
            "observation_skew_s": self.observation_skew_s,
            "max_observation_age_s": self.max_observation_age_s,
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

    if name == "click_sequence":
        sequence = args.get("sequence") if isinstance(args, dict) else None
        if not isinstance(sequence, (list, tuple)) or not sequence:
            return FlashPrecondition(
                False, "coordinate_space_mismatch", "click_sequence needs a non-empty sequence"
            )
        for position, point in enumerate(sequence, start=1):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return FlashPrecondition(
                    False,
                    "coordinate_space_mismatch",
                    f"sequence entry {position} is not a point",
                )
            try:
                x, y = int(point[0]), int(point[1])
            except (TypeError, ValueError):
                return FlashPrecondition(
                    False,
                    "coordinate_space_mismatch",
                    f"sequence entry {position} is invalid",
                )
            if not (0 <= x <= 1000 and 0 <= y <= 1000):
                return FlashPrecondition(
                    False,
                    "coordinate_space_mismatch",
                    f"sequence entry {position} {x},{y} is outside 0..1000",
                )

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
    max_age = policy.max_observation_age_s
    if skew is not None and max_age is not None and skew > max_age:
        return FlashPrecondition(
            False,
            "stale_frame",
            f"observation is {skew:.1f}s old (max {max_age:.1f}s for {name})",
            observation_skew_s=skew,
            max_observation_age_s=max_age,
            pointer_precheck=pointer_precheck,
        )
    return FlashPrecondition(
        True,
        observation_skew_s=skew,
        max_observation_age_s=max_age,
        pointer_precheck=pointer_precheck,
    )


#: A Flash turn is still a bounded batch, not an unbounded model-controlled loop.
MAX_FLASH_ACTIONS_PER_TURN = 8
#: High-risk actions get their own, smaller ceiling inside the turn budget.
MAX_FLASH_HIGH_RISK_ACTIONS_PER_TURN = 2
MAX_FLASH_CLICKS_PER_TURN = 3
MAX_FLASH_RISK_UNITS = 8

#: Actions that may appear in a budgeted sequential Flash turn. Flash observes
#: after each action, so this is not Pro's unvetted fast-action burst. The
#: allowlist is the single source of truth: it decides admission to a burst,
#: while the runner separately counts and checks *every* device action.
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
    max_clicks: int = MAX_FLASH_CLICKS_PER_TURN
    max_risk_units: int = MAX_FLASH_RISK_UNITS
    allowed_actions: frozenset[str] = FLASH_BURST_ACTIONS

    def allows(
        self,
        action: str,
        position: int,
        *,
        clicks: int = 0,
        risk_units: int = 0,
    ) -> bool:
        """Whether this action may run at these actual click/risk positions."""

        name = str(action)
        if name not in self.allowed_actions:
            return False
        if not 1 <= int(position) <= max(1, int(self.max_actions)):
            return False
        if int(clicks) > max(1, int(self.max_clicks)):
            return False
        if int(risk_units) > max(1, int(self.max_risk_units)):
            return False
        return True


def flash_burst_budget() -> FlashBurstBudget:
    return FlashBurstBudget()
