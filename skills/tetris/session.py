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
    """Forget everything the previous session taught us, counters included."""
    _forget()
    _reset_counters()


def stop(*_args, **_kwargs) -> None:
    """Forget the frames and the decision on the way out — but keep the report.

    The counters are the *record* of what the session did (how many paid calls, how many
    fell back to the search); zeroing them here wiped the answer to the plan's budget
    question at the exact moment the caller reads it. They are reset on the way *in*
    instead, which is what makes a second session start from nothing.
    """
    _forget()


def _forget() -> None:
    from . import perception, policy_jev

    perception.reset_history()
    policy_jev.forget_decision()


def _reset_counters() -> None:
    from . import policy_jev

    policy_jev.reset_telemetry()


__all__ = ["start", "stop"]
