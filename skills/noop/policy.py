"""Decide nothing, count frames. The point is the measurement, not the play."""

from __future__ import annotations

from artemis.reflex.contract import Action

_seen = 0


def decide(state, calibration):
    """Return an action with no gestures, ending the session after N frames if asked."""
    global _seen
    _seen += 1
    stop_after = int((calibration or {}).get("stop_after_frames") or 0)
    return Action(
        gestures=(),
        done=bool(stop_after and _seen >= stop_after),
        state={"counters": {"frames_seen": _seen, "frame_kind": state.get("kind")}},
    )
