"""The closed loop: capture → decide → act, on a budget, with numbers.

Deliberate properties, each one the answer to something the framework does *not*
do well at this cadence:

* **No model.** A skill's policy is deterministic code. The agent decides whether
  to run, for how long, and what to make of the result.
* **No per-frame database writes.** ``observe()`` persists every capture (a step
  row, an image, a trace); at 10 Hz for a minute that is 600 images. Reflex
  frames stay in memory; only the session's summary is recorded.
* **No safety net.** The precondition gate that guards a single high-value tap is
  pure overhead in a loop that acts 5–10 times a second.
* **Bounded and honest.** Blind frames and control failures are counted, and the
  loop stops when either runs away, instead of acting on a screen it cannot read.

The loop is transport-agnostic: capture and control come in as objects, so the
same runtime drives a device, a recording, or a fake in a test.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from typing import Any

from artemis.reflex.contract import (
    Action,
    Capture,
    CaptureError,
    Control,
    Frame,
    ReflexBudget,
    ReflexResult,
    SkillHandle,
    StopReason,
    now,
)
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: Default budget (ADR-007); kept as module constants for old callers.
DEFAULT_BUDGET = ReflexBudget()
DEFAULT_MAX_BLIND_FRAMES = DEFAULT_BUDGET.max_blind_frames
DEFAULT_MAX_CONTROL_FAILURES = DEFAULT_BUDGET.max_control_failures
DEFAULT_FPS_CAP = DEFAULT_BUDGET.fps_cap

#: What a *skill* may plausibly raise (CV, JSON, shape and index mistakes). Narrow
#: on purpose: a skill is our own plugin code, so an unexpected exception type is a
#: defect worth surfacing rather than folding into "the skill failed".
_SKILL_ERRORS = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    RuntimeError,
    OSError,
    ImportError,
)

#: What a gesture dispatch may raise: our controls wrap transport failures, and a
#: skill-supplied control is expected to do the same.
_CONTROL_ERRORS = (RuntimeError, OSError, ValueError, TimeoutError)


class ReflexRuntime:
    """Runs one skill against one device for a bounded time."""

    def __init__(
        self,
        *,
        capture: Capture,
        control: Control,
        fps_cap: float = DEFAULT_FPS_CAP,
        max_blind_frames: int = DEFAULT_MAX_BLIND_FRAMES,
        max_control_failures: int = DEFAULT_MAX_CONTROL_FAILURES,
        budget: ReflexBudget | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.capture = capture
        self.control = control
        self.budget = budget or ReflexBudget(
            fps_cap=fps_cap,
            max_blind_frames=max_blind_frames,
            max_control_failures=max_control_failures,
        )
        self.fps_cap = max(0.5, float(self.budget.fps_cap))
        self.max_blind_frames = max(1, int(self.budget.max_blind_frames))
        self.max_control_failures = max(1, int(self.budget.max_control_failures))
        apply_budget = getattr(control, "apply_budget", None)
        if callable(apply_budget):
            apply_budget(self.budget)
        self._sleep = sleep

    def run(
        self,
        skill: SkillHandle,
        *,
        seconds: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> ReflexResult:
        """Drive ``skill`` for at most ``seconds`` (or until it reports done)."""
        started = now()
        run_seconds = self.budget.max_seconds if seconds is None else float(seconds)
        deadline = started + max(0.0, run_seconds)
        min_interval = 1.0 / self.fps_cap

        capture_ms: list[float] = []
        action_ms: list[float] = []
        frames = 0
        actions = 0
        blind = 0
        control_failures = 0
        consecutive_control_failures = 0
        stop = StopReason.TIME_BUDGET
        error: str | None = None
        final_state: Any = None
        counters: dict[str, Any] = {}
        incident: str | None = None
        incidents: list[str] = []

        def note_incident(value: str | None) -> None:
            if value is None:
                return
            if value not in incidents:
                incidents.append(value)

        skill.started()
        try:
            while True:
                loop_started = now()
                if loop_started >= deadline:
                    stop = StopReason.TIME_BUDGET
                    break
                if should_stop is not None and should_stop():
                    stop = StopReason.CANCELLED
                    break

                try:
                    frame = self.capture.grab()
                except CaptureError as exc:
                    blind += 1
                    capture_incident = getattr(exc, "incident", None) or "capture_timeout"
                    note_incident(capture_incident)
                    logger.debug(f"reflex[{skill.name}]: capture failed: {exc}")
                    if blind >= self.max_blind_frames:
                        incident = capture_incident
                        stop = StopReason.LOST
                        break
                    self._sleep(min_interval)
                    continue

                # A blocking capture can return after the deadline; do not act on
                # that frame. The adapter's own timeout bounds the overshoot.
                if now() >= deadline:
                    stop = StopReason.TIME_BUDGET
                    break
                frames += 1
                capture_ms.append(frame.capture_ms)

                try:
                    state = skill.parse(frame)
                except _SKILL_ERRORS as exc:
                    stop = StopReason.SKILL_ERROR
                    error = f"perception failed: {exc}"
                    break

                if not state:
                    blind += 1
                    if blind >= self.max_blind_frames:
                        incident = "target_unverified"
                        note_incident(incident)
                        stop = StopReason.LOST
                        break
                    self._sleep_remaining(loop_started, min_interval)
                    continue
                blind = 0
                final_state = state

                try:
                    action = skill.decide(state, skill.calibration)
                except _SKILL_ERRORS as exc:
                    stop = StopReason.SKILL_ERROR
                    error = f"policy failed: {exc}"
                    break
                if not isinstance(action, Action):
                    stop = StopReason.SKILL_ERROR
                    error = f"policy returned {type(action).__name__}, expected Action"
                    break
                if isinstance(action.state, dict) and action.state.get("counters"):
                    counters.update(action.state["counters"])

                try:
                    materialised = (
                        tuple(action.gestures)
                        if action.gestures
                        else tuple(skill.to_gestures(action) or ())
                    )
                except _SKILL_ERRORS as exc:
                    stop = StopReason.SKILL_ERROR
                    error = f"control failed: {exc}"
                    break
                for gesture in materialised:
                    try:
                        action_ms.append(float(self.control.dispatch(gesture)))
                        actions += 1
                        consecutive_control_failures = 0
                    except _CONTROL_ERRORS as exc:
                        control_failures += 1
                        consecutive_control_failures += 1
                        control_incident = getattr(exc, "incident", None) or "transport_error"
                        note_incident(control_incident)
                        logger.warning(f"reflex[{skill.name}]: gesture failed: {exc}")
                        if consecutive_control_failures >= self.max_control_failures:
                            incident = control_incident
                            stop = StopReason.CONTROL_LOST
                            break
                if stop is StopReason.CONTROL_LOST:
                    break

                if action.done:
                    stop = StopReason.SKILL_DONE
                    break

                self._sleep_remaining(loop_started, min_interval)
        finally:
            skill.stopped()
            self._release_inputs(skill.name)
            try:
                self.capture.close()
            except (OSError, RuntimeError, ValueError) as exc:
                logger.debug(f"reflex[{skill.name}]: capture close failed: {exc}")

        elapsed = max(1e-6, now() - started)
        if incident is None:
            incident = {
                StopReason.TIME_BUDGET: "deadline_exceeded",
                StopReason.CANCELLED: "cancelled",
                StopReason.LOST: "target_unverified",
                StopReason.CONTROL_LOST: "transport_error",
            }.get(stop)
        return ReflexResult(
            skill=skill.name,
            stop_reason=stop,
            seconds=elapsed,
            frames=frames,
            actions=actions,
            capture_ms_p50=_percentile(capture_ms, 50),
            capture_ms_p95=_percentile(capture_ms, 95),
            action_ms_p50=_percentile(action_ms, 50),
            action_ms_p95=_percentile(action_ms, 95),
            fps=frames / elapsed,
            blind_frames=blind,
            control_failures=control_failures,
            skill_error=error,
            final_state=final_state,
            counters=counters,
            budget=self.budget,
            incident=incident,
            incidents=tuple(incidents),
        )

    def _release_inputs(self, skill_name: str) -> None:
        """Best-effort release of keys/buttons after any exit path (ADR-007 D4)."""

        release = getattr(self.control, "release_inputs", None)
        if not callable(release):
            return
        try:
            release()
        except (OSError, RuntimeError, ValueError, AttributeError) as exc:
            logger.warning(f"reflex[{skill_name}]: release_inputs failed: {exc}")

    def _sleep_remaining(self, loop_started: float, min_interval: float) -> None:
        remaining = min_interval - (now() - loop_started)
        if remaining > 0:
            self._sleep(remaining)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    index = (len(ordered) - 1) * (pct / 100.0)
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    weight = index - low
    return float(ordered[low] * (1 - weight) + ordered[high] * weight)


def run_reflex(
    skill: SkillHandle,
    *,
    capture: Capture,
    control: Control,
    seconds: float | None = None,
    fps_cap: float = DEFAULT_FPS_CAP,
    budget: ReflexBudget | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ReflexResult:
    """Convenience wrapper: one skill, one bounded session."""
    runtime = ReflexRuntime(
        capture=capture,
        control=control,
        fps_cap=fps_cap,
        budget=budget,
    )
    return runtime.run(skill, seconds=seconds, should_stop=should_stop)


__all__ = [
    "DEFAULT_FPS_CAP",
    "DEFAULT_MAX_BLIND_FRAMES",
    "DEFAULT_BUDGET",
    "DEFAULT_MAX_CONTROL_FAILURES",
    "ReflexRuntime",
    "ReflexBudget",
    "run_reflex",
]
