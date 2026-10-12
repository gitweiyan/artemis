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
