"""The data an inner loop passes around: one frame in, one action out.

Everything here is plain data with no framework imports, so a skill can be
developed and regression-tested offline (synthetic frames, recorded frame
sequences) without a device, a session or a database.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


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
class Frame:
    """One captured screen, as the skill's perception wants it."""

    #: JPEG/PNG bytes as the device produced them — never re-encoded per frame.
    image: bytes
    #: Device clock-free capture stamp (host monotonic seconds), for budgets.
    captured_at: float
    #: Monotonic seconds spent getting this frame (capture + transfer + decode).
    capture_ms: float
    #: Screen geometry, when the capture path knows it.
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class Gesture:
    """One device interaction a skill can ask for.

    Kept deliberately tiny: tap and swipe cover every mobile game and most
    scroll-heavy flows. ``description`` is carried into the metrics so a reader
    can see what the loop was doing without replaying the video.
    """

    kind: str  # "tap" | "swipe"
    points: tuple[tuple[float, float], ...]
    duration_ms: int = 120
    description: str = ""

    @staticmethod
    def tap(x: float, y: float, *, description: str = "") -> Gesture:
        return Gesture(kind="tap", points=((float(x), float(y)),), description=description)

    @staticmethod
    def swipe(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        duration_ms: int = 120,
        description: str = "",
    ) -> Gesture:
        return Gesture(
            kind="swipe",
            points=((float(x1), float(y1)), (float(x2), float(y2))),
            duration_ms=int(duration_ms),
            description=description,
        )


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
        if self.skill_error:
            head += f" | skill error: {self.skill_error}"
        return head


class Capture(Protocol):
    """What the runtime needs from a frame source (device or recording)."""

    def grab(self) -> Frame:
        """Return one frame; raise :class:`CaptureError` when none is usable."""

    def close(self) -> None:
        """Release whatever the capture holds."""


class Control(Protocol):
    """What the runtime needs from an actuator."""

    def dispatch(self, gesture: Gesture) -> float:
        """Perform one gesture; return its latency in milliseconds."""


class CaptureError(RuntimeError):
    """No usable frame (device gone, decode failed, session dropped)."""


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
