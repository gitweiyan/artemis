"""Reflex runtime: a fast, deterministic inner loop an agent can hand a device to.

Why this exists
---------------
Artemis' operator loop decides in seconds (measured: ~3.6 s per step on a real
device: 400 ms settling + capture + OCR + a DB write + one LLM round trip) and
guards every action with a safety net worth up to ~7 s. That cadence is fine for
"open the settings page"; it cannot play a game, drive a scroll-heavy crawl, or
handle anything where the screen moves while the model thinks.

The reflex layer is the other regime: no model in the loop, a fixed budget, and
deterministic code doing the work. The agent decides *whether* and *for how long*
to run a reflex skill; the skill decides what to do frame by frame.

Framework side (this package) is generic and game-agnostic — three pieces:

* :mod:`artemis.reflex.registry` — find and validate skills from disk;
* :mod:`artemis.reflex.runtime` — the closed loop (capture → decide → act), with
  per-frame captures that never touch the database and metrics that describe the
  run (fps, percentiles, degradation counts, stop reason);
* :mod:`artemis.reflex.capture` / :mod:`artemis.reflex.control` — the only two
  places that touch the device.

Skill side lives outside this package (``skills/<name>/`` by default): its
perception (pixels → state), policy (state → action) and control (action →
gestures) are pure functions, plus a calibration file that holds everything
device- and app-specific. Adding a skill must never mean editing this package.
"""

from __future__ import annotations

from artemis.reflex.contract import (
    Action,
    Frame,
    Gesture,
    ReflexBudget,
    ReflexResult,
    SkillHandle,
    StopReason,
)
from artemis.reflex.registry import (
    SkillError,
    available_skills,
    load_skill,
    skills_dir,
)
from artemis.reflex.runtime import ReflexRuntime, run_reflex

__all__ = [
    "Action",
    "Frame",
    "Gesture",
    "ReflexBudget",
    "ReflexResult",
    "ReflexRuntime",
    "SkillError",
    "SkillHandle",
    "StopReason",
    "available_skills",
    "load_skill",
    "run_reflex",
    "skills_dir",
]
