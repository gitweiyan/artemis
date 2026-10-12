"""Per-session state, and the hook that clears it.

Two things in this skill remember across frames *on purpose*:

* the frame **history** in :mod:`perception`, which is what makes motion measurable;
* the last **decision** in :mod:`policy_jev`, which is what keeps the model from being
  asked about the same piece once per frame.

Both are session state. A second session in the same process — a restart flow, a second
``play_reflex`` call, a test that runs two games — must start from nothing: a replayed
decision would rotate and drop a piece that is not the one it was chosen for, and a stale
frame would make the first piece of the new game look like it moved.

The runtime calls this through the manifest (``hooks.on_start``), with the skill handle as
its only argument, so the hook takes anything and ignores it.
"""

from __future__ import annotations


def start(*_args, **_kwargs) -> None:
    """Forget everything the previous session taught us."""
    _forget()


def stop(*_args, **_kwargs) -> None:
    """The same, on the way out: a session's state dies with the session."""
    _forget()


def _forget() -> None:
    from . import perception, policy_jev

    perception.reset_history()
    policy_jev.reset_telemetry()


__all__ = ["start", "stop"]
