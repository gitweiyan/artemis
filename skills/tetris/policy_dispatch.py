"""Which policy plays: the deterministic search, or System One (Jev).

Kept as its own module so both policies stay independent and testable, and so the
switch is one calibrated string: ``calibration.json`` → ``"policy_backend"``.

    "policy_backend": "search"     # default: deterministic, offline, 16 ms/decision
    "policy_backend": "systemone"  # Jev picks among the legal placements
    "policy_backend": "jev"        # the same, driven by calibration.jev (noul/choice)

Either way the *legality* boundary is the simulator's: a model cannot invent a move.
With ``systemone`` the search still plays whenever the call cannot be made (no key,
429, timeout, bad answer) or the model abstains, and the counters say which one it
was — so a comparison never silently mixes the two.
"""

from __future__ import annotations

from dataclasses import replace

from artemis.reflex.contract import Action

from . import policy as search


def decide(state, calibration):
    options = (calibration or {}).get("jev") or {}
    # Two option spaces share the Jev backend: `placement` (a rotation + a column, executed
    # over two frames) and `action` (one of the four buttons, executed as one tap). The
    # calibration picks, so the switch is one line and the fallback path is untouched.
    if str(options.get("option_space") or "placement").lower() == "action":
        from . import policy_actions

        return _with_calibration(policy_actions.decide(state, calibration), calibration)
    if _still_appearing(state):
        # The game draws a piece over two frames and the tracker acquires it on the first,
        # so a placement decided now is a placement for a shape that is still growing.
        # Measured on the device 2026-10-13 (three T8 runs): those land where planned only
        # 48% of the time (15/31), against 88% (14/16) once the shape has settled. Wait one
        # frame - the piece is at the top and has the whole board to fall.
        counters = dict((state or {}).get("counters") or {})
        counters["waiting_for_piece"] = 1
        return _with_calibration(
            Action(
                gestures=(),
                done=False,
                state={
                    "counters": counters,
                    "phase": "appear",
                    "note": "waiting for the piece to finish appearing",
                },
            ),
            calibration,
        )
    backend = str((calibration or {}).get("policy_backend") or "search").lower()
    if backend == "jev":
        from . import policy_jev

        action = policy_jev.decide(state, calibration)
    elif backend == "systemone":
        from . import policy_systemone

        action = policy_systemone.decide(state, calibration)
    else:
        action = search.decide(state, calibration)
    return _with_calibration(action, calibration)


def _still_appearing(state) -> bool:
    """Whether the piece was just acquired and its shape may still be growing.

    ``piece_frames`` comes from :mod:`tracker` through ``perception``. A state without it
    (an older pipeline, the letter path) is decided as before rather than frozen.
    """
    if not isinstance(state, dict):
        return False
    frames = state.get("piece_frames")
    if frames is None:
        return False
    try:
        return int(frames) == 1
    except (TypeError, ValueError):
        return False


def _with_calibration(action, calibration):
    """Attach the calibration to the action — the only way the control layer receives it.

    The runtime calls ``to_gestures(action)``: the action alone, no calibration, no state
    (see ``ReflexRuntime.run``), and ``control.to_gestures`` documents exactly that — "the
    calibration is attached to the action's state by the policy". Nothing attached it, so
    every frame decided correctly and dispatched nothing: the first live run played 16 s
    with ``actions: 0`` and never moved a piece. Doing it once, here, on the way out of
    whichever backend ran, keeps that from depending on each policy remembering.
    """
    if not isinstance(action, Action) or not calibration:
        return action
    state = dict(action.state or {})
    if state.get("calibration") is calibration:
        return action
    state["calibration"] = calibration
    return replace(action, state=state)


__all__ = ["decide"]
