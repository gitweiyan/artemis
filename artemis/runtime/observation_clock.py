"""The observation→dispatch skew: how stale the screen behind an action was.

Every action is planned against a screen that was captured some seconds earlier,
because an operator turn is an LLM call plus the safety net. On 2026-10-09 that
interval measured 2.4–8.5 s by hand from log timestamps, and the one failure it
produced (a splash ad's "skip" button that had already dismissed itself) could
only be classified by reading the model's own explanation. This module records
the interval per action so the race is data instead of narrative.

Design: the capture stamp and the resulting skew ride the **run context** (next
to ``ctx.transcript_ledger`` / ``ctx.ui_adb_client``), not ``State`` — the state
contract and its per-node visibility manifest stay untouched.

Two numbers exist and they answer different questions:

* the **step record** carries the interval measured when the turn was recorded —
  "how old was the screen when this decision was booked" (the turn record is
  written before the action is dispatched, so this is the largest interval that
  can be attributed to that step);
* the **dispatch** call (``note_dispatch_skew``) warns with the interval at the
  moment the action actually left the operator, which additionally includes the
  safety net's own retries — the number that matters when a target turns out to
  be gone.
"""

from __future__ import annotations

import time
from typing import Any
from typing import Protocol

from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: An interval beyond this is worth a warning; the screen changes on the scale of
#: a splash screen or a loading spinner at roughly this range.
STALE_OBSERVATION_WARN_SECONDS = 5.0

_CAPTURED_AT_ATTR = "observation_captured_at_monotonic"
_SKEW_ATTR = "observation_skew_s"
_THRESHOLD_ATTR = "stale_observation_warn_s"


class _ClockContext(Protocol):
    """Anything the run hands around (``ArtemisContext`` in production)."""


def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now


def _stamp(ctx: Any, attribute: str, value: Any) -> bool:
    """Best-effort attribute write: a context that refuses one has no clock."""
    try:
        setattr(ctx, attribute, value)
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def stamp_capture(ctx: Any, *, now: float | None = None) -> float:
    """Record that the screen data behind the current turn was captured now."""
    moment = _now(now)
    _stamp(ctx, _CAPTURED_AT_ATTR, moment)
    return moment


def captured_at(ctx: Any) -> float | None:
    """Monotonic stamp of the newest observation, or ``None`` when there is none."""
    value = getattr(ctx, _CAPTURED_AT_ATTR, None)
    return float(value) if isinstance(value, (int, float)) else None


def warn_after_seconds(ctx: Any) -> float:
    """The threshold for this run: the context's override, else the default."""
    value = getattr(ctx, _THRESHOLD_ATTR, None)
    return (
        float(value)
        if isinstance(value, (int, float)) and value > 0
        else STALE_OBSERVATION_WARN_SECONDS
    )


def skew_s(ctx: Any, *, now: float | None = None) -> float | None:
    """Seconds between the newest observation and ``now``.

    ``None`` means "no verdict": a turn that observed nothing of its own must not
    invent a number.
    """
    captured = captured_at(ctx)
    if captured is None:
        return None
    return max(0.0, round(_now(now) - captured, 3))


def note_dispatch_skew(ctx: Any, *, now: float | None = None) -> float | None:
    """Record the skew on the context and warn when the observation is stale."""
    skew = skew_s(ctx, now=now)
    if skew is None:
        return None
    _stamp(ctx, _SKEW_ATTR, skew)
    threshold = warn_after_seconds(ctx)
    if skew > threshold:
        logger.warning(
            f"Stale observation: this action left the operator {skew:.1f}s after the screen it "
            f"was planned from (threshold {threshold:.1f}s). If the target is gone or moved, the "
            f"screen changed during that window rather than the model misreading it."
        )
    return skew


def skew_metadata(ctx: Any, *, now: float | None = None) -> dict[str, float]:
    """``{"observation_skew_s": …}`` measured **now**, or ``{}`` without a stamp.

    Measured rather than read back from the dispatch-time value on purpose: the
    turn record is written *before* its action is dispatched, so a stored value
    belongs to the previous turn and would shift every number by one step.
    """
    skew = skew_s(ctx, now=now)
    return {"observation_skew_s": skew} if skew is not None else {}


__all__ = [
    "STALE_OBSERVATION_WARN_SECONDS",
    "captured_at",
    "note_dispatch_skew",
    "skew_metadata",
    "skew_s",
    "stamp_capture",
    "warn_after_seconds",
]
