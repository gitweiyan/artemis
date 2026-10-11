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
    ReflexResult,
    SkillHandle,
    StopReason,
    now,
)
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: Frames the skill may fail to parse before the loop hands control back.
DEFAULT_MAX_BLIND_FRAMES = 15
#: Consecutive gesture-dispatch failures that mean the device stopped listening.
DEFAULT_MAX_CONTROL_FAILURES = 2
#: Safety cap on loop rate; a skill may ask for less, never for more.
DEFAULT_FPS_CAP = 10.0

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
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.capture = capture
        self.control = control
        self.fps_cap = max(0.5, float(fps_cap))
        self.max_blind_frames = max(1, int(max_blind_frames))
        self.max_control_failures = max(1, int(max_control_failures))
        self._sleep = sleep

    def run(
        self,
        skill: SkillHandle,
        *,
        seconds: float = 60.0,
        should_stop: Callable[[], bool] | None = None,
    ) -> ReflexResult:
        """Drive ``skill`` for at most ``seconds`` (or until it reports done)."""
        started = now()
        deadline = started + max(0.0, float(seconds))
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
                    logger.debug(f"reflex[{skill.name}]: capture failed: {exc}")
                    if blind >= self.max_blind_frames:
                        stop = StopReason.LOST
                        break
                    self._sleep(min_interval)
                    continue

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
                        logger.warning(f"reflex[{skill.name}]: gesture failed: {exc}")
                        if consecutive_control_failures >= self.max_control_failures:
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
            try:
                self.capture.close()
            except (OSError, RuntimeError, ValueError) as exc:
                logger.debug(f"reflex[{skill.name}]: capture close failed: {exc}")

        elapsed = max(1e-6, now() - started)
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
        )

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
    seconds: float = 60.0,
    fps_cap: float = DEFAULT_FPS_CAP,
    should_stop: Callable[[], bool] | None = None,
) -> ReflexResult:
    """Convenience wrapper: one skill, one bounded session."""
    runtime = ReflexRuntime(capture=capture, control=control, fps_cap=fps_cap)
    return runtime.run(skill, seconds=seconds, should_stop=should_stop)


__all__ = [
    "DEFAULT_FPS_CAP",
    "DEFAULT_MAX_BLIND_FRAMES",
    "DEFAULT_MAX_CONTROL_FAILURES",
    "ReflexRuntime",
    "run_reflex",
]
