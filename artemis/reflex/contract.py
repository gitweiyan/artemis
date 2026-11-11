"""The data an inner loop passes around: one frame in, one action out.

Everything here is plain data with no framework imports, so a skill can be
developed and regression-tested offline (synthetic frames, recorded frame
sequences) without a device, a session or a database.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from artemis.core.reflex_contract import Capture, CaptureError, Control, Frame, Gesture


class StopReason(str, Enum):
    """Why a reflex session ended. Always reported with the metrics."""

    #: The skill reported the run is over (its own success condition, e.g. the
    #: goal metric was reached or the game ended).
    SKILL_DONE = "skill_done"
    #: The wall-clock budget the caller set ran out. Not a failure.
    TIME_BUDGET = "time_budget"
    #: The caller asked to stop (cancel, new task, user action).
    CANCELLED = "cancelled"
    #: Perception could not hand back a usable state for ``max_blind_frames``.
    LOST = "lost"
    #: The device stopped accepting actions (gesture dispatch failed twice in a
    #: row): the loop stops rather than flailing, and hands control back.
    CONTROL_LOST = "control_lost"
    #: The skill raised. The loop reports it instead of dying with it.
    SKILL_ERROR = "skill_error"


@dataclass(frozen=True)
class ReflexBudget:
    """The bounded safety budget of one high-frequency Reflex session.

    Task-mode Flash/Pro use the slower ActionGate/Checker instead. These values are
    deliberately explicit so a run can be replayed from its summary; the defaults
    preserve the pre-ADR-007 behavior.
    """

    max_seconds: float = 60.0
    fps_cap: float = 10.0
    max_blind_frames: int = 15
    max_control_failures: int = 2
    max_frame_age_s: float = 3.0
    max_action_attempts: int = 3
    max_move_steps: int = 6
    operation_timeout_s: float = 10.0

    def __post_init__(self) -> None:
        seconds = float(self.max_seconds)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("max_seconds must be finite and positive")
        if float(self.fps_cap) <= 0:
            raise ValueError("fps_cap must be positive")
        for name in (
            "max_blind_frames",
            "max_control_failures",
            "max_action_attempts",
            "max_move_steps",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if float(self.max_frame_age_s) <= 0:
            raise ValueError("max_frame_age_s must be positive")
        if float(self.operation_timeout_s) <= 0:
            raise ValueError("operation_timeout_s must be positive")
        object.__setattr__(self, "max_seconds", seconds)
        object.__setattr__(self, "fps_cap", float(self.fps_cap))
        for name in (
            "max_blind_frames",
            "max_control_failures",
            "max_action_attempts",
            "max_move_steps",
        ):
            object.__setattr__(self, name, int(getattr(self, name)))
        object.__setattr__(self, "max_frame_age_s", float(self.max_frame_age_s))
        object.__setattr__(self, "operation_timeout_s", float(self.operation_timeout_s))

    def as_dict(self) -> dict[str, float | int]:
        return {
            "max_seconds": self.max_seconds,
            "fps_cap": self.fps_cap,
            "max_blind_frames": self.max_blind_frames,
            "max_control_failures": self.max_control_failures,
            "max_frame_age_s": self.max_frame_age_s,
            "max_action_attempts": self.max_action_attempts,
            "max_move_steps": self.max_move_steps,
            "operation_timeout_s": self.operation_timeout_s,
        }


@dataclass(frozen=True)
class Action:
    """What a skill decided to do with the current frame.

    ``done`` lets a skill end its own session (goal reached, game over);
    ``state`` is whatever the skill's perception produced, kept for the metrics
    and for the caller's report — the framework never interprets it.
    """

    gestures: tuple[Gesture, ...] = ()
    done: bool = False
    state: Any = None
    note: str = ""


@dataclass
class ReflexResult:
    """The report of one reflex session: what happened, with numbers."""

    skill: str
    stop_reason: StopReason
    seconds: float
    frames: int
    actions: int
    capture_ms_p50: float
    capture_ms_p95: float
    action_ms_p50: float
    action_ms_p95: float
    fps: float
    blind_frames: int
    control_failures: int
    skill_error: str | None = None
    #: Last state the skill reported (its own structure; the caller renders it).
    final_state: Any = None
    #: Free-form counters a skill wants reported (lines cleared, score, …).
    counters: dict[str, Any] = field(default_factory=dict)
    #: The exact budget this run executed under (None only for legacy callers).
    budget: ReflexBudget | None = None
    #: Canonical §7.2 incident for the stop cause, or None for a clean finish.
    incident: str | None = None
    #: Bounded, de-duplicated incidents observed in the session.
    incidents: tuple[str, ...] = ()

    @property
    def achieved(self) -> bool:
        """Whether the skill ended the session on its own terms."""
        return self.stop_reason is StopReason.SKILL_DONE

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "skill": self.skill,
            "stop_reason": self.stop_reason.value,
            "seconds": round(self.seconds, 3),
            "frames": self.frames,
            "actions": self.actions,
            "fps": round(self.fps, 2),
            "capture_ms_p50": round(self.capture_ms_p50, 1),
            "capture_ms_p95": round(self.capture_ms_p95, 1),
            "action_ms_p50": round(self.action_ms_p50, 1),
            "action_ms_p95": round(self.action_ms_p95, 1),
            "blind_frames": self.blind_frames,
            "control_failures": self.control_failures,
        }
        if self.budget is not None:
            payload["budget"] = self.budget.as_dict()
        if self.incident:
            payload["incident"] = self.incident
        if self.incidents:
            payload["incidents"] = list(self.incidents)
        if self.skill_error:
            payload["skill_error"] = self.skill_error
        if self.counters:
            payload["counters"] = dict(self.counters)
        return payload

    def summary_line(self) -> str:
        """One line for a log or a tool result."""
        head = (
            f"reflex[{self.skill}] {self.stop_reason.value} in {self.seconds:.1f}s: "
            f"{self.frames} frames ({self.fps:.1f} fps), {self.actions} actions, "
            f"capture p50 {self.capture_ms_p50:.0f} ms / p95 {self.capture_ms_p95:.0f} ms, "
            f"action p50 {self.action_ms_p50:.0f} ms"
        )
        if self.counters:
            pairs = ", ".join(f"{k}={v}" for k, v in self.counters.items())
            head += f" | {pairs}"
        if self.incident:
            head += f" | incident={self.incident}"
        if self.skill_error:
            head += f" | skill error: {self.skill_error}"
        return head


@dataclass
class SkillHandle:
    """A loaded skill: its manifest plus the pure callables it exposes.

    ``parse`` must return something falsy when the screen is not in a state the
    skill can act on (menu, ad, loading). The runtime counts those frames as
    *blind* and, past a threshold, stops — that is how a game that ends up on an
    ad screen hands control back instead of flailing at it.

    Division of labour: ``decide`` says *what* should happen (an ``Action``, whose
    ``state`` carries the skill's own decision), ``to_gestures`` says *how* it is
    expressed as taps and swipes. A policy may fill ``Action.gestures`` directly;
    when it does not, the runtime asks ``to_gestures``.
    """

    name: str
    version: str
    directory: Path
    parse: Any
    decide: Any
    to_gestures: Any
    calibration: dict[str, Any] = field(default_factory=dict)
    #: Optional one-line hint the caller may put in front of a planner.
    plan_hint: str = ""
    #: Optional capability requirements, validated by the registry.
    requires: dict[str, Any] = field(default_factory=dict)
    #: Optional greeting/cleanup hooks; both are called by the runtime.
    on_start: Any = None
    on_stop: Any = None
    #: Optional offline arena entry point (``run_tournament(**kwargs)``): lets a
    #: skill be compared against itself, or against another policy, with no device.
    arena: Any = None

    def started(self, now: float | None = None) -> None:
        if callable(self.on_start):
            self.on_start(self)

    def stopped(self) -> None:
        if callable(self.on_stop):
            self.on_stop(self)


def now() -> float:
    """Monotonic seconds — budgets must not move when the wall clock does."""
    return time.monotonic()
