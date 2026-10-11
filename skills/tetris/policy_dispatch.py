"""Which policy plays: the deterministic search, or System One (Jev).

Kept as its own module so both policies stay independent and testable, and so the
switch is one calibrated string: ``calibration.json`` → ``"policy_backend"``.

    "policy_backend": "search"     # default: deterministic, offline, 16 ms/decision
    "policy_backend": "systemone"  # Jev picks among the legal placements

Either way the *legality* boundary is the simulator's: a model cannot invent a move.
With ``systemone`` the search still plays whenever the call cannot be made (no key,
429, timeout, bad answer) or the model abstains, and the counters say which one it
was — so a comparison never silently mixes the two.
"""

from __future__ import annotations

from . import policy as search


def decide(state, calibration):
    backend = str((calibration or {}).get("policy_backend") or "search").lower()
    if backend == "systemone":
        from . import policy_systemone

        return policy_systemone.decide(state, calibration)
    return search.decide(state, calibration)


__all__ = ["decide"]
