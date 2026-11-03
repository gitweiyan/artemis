"""Mouse Controller v2: a target in, relative HID moves + click + verification out.

The controller is deliberately camera-grounded: it never trusts its own dx/dy
integration. Each attempt re-observes the pointer, rejects low-confidence
readings, moves in a coarse step, and clicks only when the click visibly changed
the screen.

Coordinates are normalized to the captured frame (0..1, origin top-left), so a
crop/resolution change does not change a caller's target.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from artemis.reflex.motion import MotionCalibration, MotionConfig, Phase, choose_phase
from artemis.reflex.vision import (
    POINTER_RADIUS_PX,
    Pointer,
    changed,
    corridor_roi,
    detect_in_roi,
)
from artemis.safety.pixel import verify_effect
from artemis.safety.pointer import check_point


@dataclass
class MotionReport:
    """What the phased planner did during one travel (logs, metrics, the bench)."""

    segments: int = 0
    observations: int = 0
    phases: list[str] = field(default_factory=list)
    prediction_error_px: float | None = None
    global_recoveries: int = 0


@dataclass(frozen=True)
class TapResult:
    """Outcome of one tap attempt.

    ``incident`` is the machine-readable reason a failed tap failed:
    ``pointer_missing`` (no verified pointer, so no click), ``pointer_not_centered``
    (the pointer never converged inside the tolerance) or ``no_effect`` (the click was
    dispatched but the screen did not change). Callers must not parse ``message``.
    """

    ok: bool
    attempts: int
    pointer: Pointer | None
    error_px: float | None
    message: str
    incident: str | None = None
    #: Age of the frame the click was verified against (seconds), when it could be dated.
    frame_age_s: float | None = None
    #: How much of the target crop changed after the click (0..1), when it could be read.
    crop_change: float | None = None
    #: The phased movement's record, when the verified path ran (never on the legacy path).
    motion: MotionReport | None = None


class PointerSession:
    """Dead-reckoned pointer state for one iOS mouse connection.

    iOS starts a newly connected pointer at the top-left and keeps clicking at the
    last position even after the cursor hides. ``home`` is verified to re-anchor
    repeatedly, so state is ``origin + calibrated HID deltas``, clamped to the screen.
    UVC is only needed for calibration/correction, not for every tap.
    """

    def __init__(
        self,
        control,
        *,
        frame_size: tuple[int, int] = (449, 974),
        gain: tuple[float, float] = (1.4, 1.4),
        home_settle_s: float = 0.8,
    ):
        if gain[0] <= 0 or gain[1] <= 0:
            raise ValueError("gain must be positive")
        self.control = control
        self.frame_size = (int(frame_size[0]), int(frame_size[1]))
        self.gain_x = float(gain[0])
        self.gain_y = float(gain[1])
        self._x = 0.0
        self._y = 0.0
        self.connected = False
        self.home_settle_s = max(0.0, float(home_settle_s))

    @property
    def position(self) -> tuple[float, float]:
        return self._x, self._y

    def ensure_home_screen(self) -> None:
        """Return to the iOS Home screen without touching the pointer state."""
        if hasattr(self.control, "ios_home"):
            self.control.ios_home()
            if self.home_settle_s > 0:
                time.sleep(self.home_settle_s)

    def connect(self) -> None:
        """Preflight, return to iOS Home, then re-anchor the pointer to the top-left."""
        self.control.preflight()
        self.ensure_home_screen()
        self.control.mouse_home()
        self._x = 0.0
        self._y = 0.0
        self.connected = True

    def home(self) -> None:
        """Re-anchor to the top-left (home is verified to zero repeatedly)."""
        self.control.mouse_home()
        self._x = 0.0
        self._y = 0.0

    def move(self, dx: int, dy: int) -> tuple[float, float]:
        if not self.connected:
            raise RuntimeError("pointer session is not connected; call connect() first")
        self.control.mouse_move(int(dx), int(dy))
        self._x = max(0.0, min(float(self.frame_size[0]), self._x + int(dx) * self.gain_x))
        self._y = max(0.0, min(float(self.frame_size[1]), self._y + int(dy) * self.gain_y))
        return self.position

    def _target(self, x: float, y: float) -> tuple[int, int]:
        width, height = self.frame_size
        if not (0.0 <= x <= width and 0.0 <= y <= height):
            raise ValueError(f"target outside frame {self.frame_size}: ({x}, {y})")
        return round((x - self._x) / self.gain_x), round((y - self._y) / self.gain_y)

    def click(self) -> None:
        """Left-click at the current state position (no move)."""
        if not self.connected:
            raise RuntimeError("pointer session is not connected; call connect() first")
        self.control.mouse_click()

    def tap(self, x: float, y: float) -> tuple[int, int]:
        """Move by the calibrated delta, then click at the current position."""
        if not self.connected:
            raise RuntimeError("pointer session is not connected; call connect() first")
        dx, dy = self._target(x, y)
        if dx or dy:
            self.move(dx, dy)
        self.click()
        return dx, dy

    def observe(self, x: float, y: float) -> None:
        """Correct dead reckoning from a trusted UVC/template reading."""
        self._x = max(0.0, min(float(self.frame_size[0]), float(x)))
        self._y = max(0.0, min(float(self.frame_size[1]), float(y)))

    def calibrate(self, samples: list[tuple[int, int, float, float]]) -> None:
        """Fit gain from ``(dx, dy, observed_x, observed_y)`` samples taken from home."""
        x_pairs = [
            (int(dx), float(observed_x)) for dx, _dy, observed_x, _observed_y in samples if dx
        ]
        y_pairs = [
            (int(dy), float(observed_y)) for _dx, dy, _observed_x, observed_y in samples if dy
        ]
        if not x_pairs or not y_pairs:
            raise ValueError("calibration needs at least one nonzero x and y sample")
        gain_x = sum(dx * observed for dx, observed in x_pairs) / sum(dx * dx for dx, _ in x_pairs)
        gain_y = sum(dy * observed for dy, observed in y_pairs) / sum(dy * dy for dy, _ in y_pairs)
        if gain_x <= 0 or gain_y <= 0:
            raise ValueError("calibration produced a non-positive gain")
        self.gain_x = gain_x
        self.gain_y = gain_y
        self._x = 0.0
        self._y = 0.0


class SessionTapController:
    """Tap with a mandatory pre-click pointer check when a detector is supplied.

    The verified path grabs a frame, detects the pointer, corrects with bounded
    moves, and clicks only when the detected pointer is within ``tolerance_px`` of
    the target. A missing pointer means no click. The legacy dead-reckoning path is
    kept for callers without a capture/detector, but it cannot pre-verify.
    """

    def __init__(
        self,
        session: PointerSession,
        *,
        capture=None,
        detector=None,
        max_attempts: int = 3,
        tolerance_px: float = 8.0,
        max_steps: int = 6,
        max_step: int = 100,
        observe_attempts: int = 3,
        wake_step: int = 60,
        settle_timeout_s: float = 0.6,
        max_frame_age_s: float = 3.0,
        tracker=None,
        diff_nudge: int = 20,
        adaptive_gain: bool = True,
        motion_config: MotionConfig | None = None,
        motion_calibration: MotionCalibration | None = None,
    ):
        if detector is not None and capture is None:
            raise ValueError("pre-click pointer verification needs a capture")
        self.session = session
        self.capture = capture
        self.detector = detector
        self.max_attempts = max(1, int(max_attempts))
        self.tolerance_px = float(tolerance_px)
        self.max_steps = max(1, int(max_steps))
        self.max_step = max(1, int(max_step))
        #: One empty frame is not proof the pointer is gone: the AssistiveTouch ring
        #: fades in and out and a frame can predate the move that woke it. Re-observe a
        #: bounded number of times before refusing to click.
        self.observe_attempts = max(1, int(observe_attempts))
        #: iOS hides the AssistiveTouch pointer after a moment of inactivity; a move of
        #: at least this many HID units reveals it again (measured 2026-10-21: 15 units
        #: is too small, 60 is reliable). The correction loop compensates the offset.
        self.wake_step = max(1, int(wake_step))
        #: How long to wait for a frame that reflects the last move. A camera (and the
        #: UVC pipeline) lags the pointer by a frame or two; re-detecting on the pre-move
        #: frame makes the correction loop chase its own tail.
        self.settle_timeout_s = max(0.0, float(settle_timeout_s))
        #: How old a verification frame may be before the tap is refused. A capture path
        #: that froze (a stray process holding the card) otherwise looks like a pointer
        #: that will not move; refusing keeps "no verified pointer, no click" true.
        self.max_frame_age_s = max(0.0, float(max_frame_age_s))
        #: Fallback locator for a pointer the colour/shape model cannot see (a grey
        #: AssistiveTouch pointer over a busy screen). ``PointerTracker.locate`` diffs
        #: two frames around a nudge and needs no appearance model at all.
        self.tracker = tracker
        self.diff_nudge = max(1, int(diff_nudge))
        #: iOS pointer gain is *not* constant: measured 1.2 per HID unit for small moves
        #: and up to ~2.1 for large ones (2026-10-21). A fixed gain makes the correction
        #: loop stop short of the target, so the ratio is estimated from the observations
        #: themselves (exponential smoothing, clamped to a plausible band).
        self.adaptive_gain = bool(adaptive_gain)
        self._gain = [float(session.gain_x), float(session.gain_y)]
        #: The phased movement policy and its per-phase gains (see artemis.reflex.motion).
        #: The calibration starts from the session's gain and is learned from observations.
        self.motion_config = motion_config or MotionConfig()
        self.motion_calibration = motion_calibration or MotionCalibration(
            (self._gain[0], self._gain[1])
        )
        #: Expected prediction error of the motion model, in frame pixels. Sizes the
        #: observation corridor: too tight and a real pointer falls outside it.
        self._sigma = 8.0
        #: How many times one tap may nudge a faded pointer back into sight.
        self.max_wakes = 2

    def _frame_age_s(self, frame) -> float | None:
        """Seconds between capture and now, when the frame can be dated."""
        captured_at = getattr(frame, "captured_at", None)
        if not isinstance(captured_at, (int, float)):
            return None
        return max(0.0, time.monotonic() - float(captured_at))

    def _stale_reason(self, frame) -> str | None:
        age = self._frame_age_s(frame)
        if age is None or self.max_frame_age_s <= 0:
            return None
        if age <= self.max_frame_age_s:
            return None
        return f"verification frame is {age:.1f}s old (budget {self.max_frame_age_s:.1f}s)"

    @staticmethod
    def _frame_reflects(frame, previous: bytes, not_before: float | None) -> bool:
        """Whether ``frame`` can show the last move: captured after it was dispatched.

        A receipt timestamp proves the frame was *received* after the move; in frame mode
        (one ffmpeg per grab) that is also when its content was taken, so the frame cannot
        be a queued pre-move picture. Captures without a timestamp fall back to the
        whole-frame diff, with the caveat that an animation elsewhere can satisfy it.
        """
        captured_at = getattr(frame, "captured_at", None)
        if not_before is not None and captured_at is not None:
            return float(captured_at) >= float(not_before)
        return changed(previous, frame.image)

    def _frame_after(self, previous: bytes, *, not_before: float | None = None):
        """The newest frame that reflects the last move (bounded wait)."""
        deadline = time.monotonic() + self.settle_timeout_s
        frame = self.capture.grab_latest()
        while not self._frame_reflects(frame, previous, not_before) and time.monotonic() < deadline:
            frame = self.capture.grab_latest()
        return frame

    def _observe_by_diff(self, frame):
        """Locate the pointer by nudging it and diffing the two frames.

        Returns ``(frame, pointer)`` or ``(frame, None)``. The nudge is part of the
        belief's own dead reckoning, so a detection (or a failure) leaves the state
        consistent.
        """
        width, height = self.session.frame_size
        near = (self.session.position[0] / width, self.session.position[1] / height)
        if frame is None:
            frame = self.capture.grab_latest()
        before = frame.image
        self.session.move(self.diff_nudge, 0)
        later = self._frame_after(before)
        shift = (self.diff_nudge * self.session.gain_x) / max(1, width)
        found = self.tracker.locate(before, later.image, near, shift)
        if found is not None:
            # ``locate`` reports where the pointer was in the *first* frame; the nudge
            # has already moved it, so report the current position.
            found = Pointer(min(1.0, found.x + shift), found.y, found.confidence, found.method)
        return later, found

    def _update_gain(self, moved: tuple[int, int], observed: tuple[float, float]) -> None:
        """Blend the measured displacement ratio into the gain estimate."""
        if not self.adaptive_gain:
            return
        for index, delta in enumerate(moved):
            if abs(delta) < 8:
                continue  # too small to measure against pointer jitter
            ratio = observed[index] / delta
            if not (0.3 <= ratio <= 4.0):
                continue  # a mis-detection, not a gain
            self._gain[index] = min(4.0, max(0.3, 0.6 * self._gain[index] + 0.4 * ratio))
        # Mirror the measurement into the session so `session.move` integrates with it.
        self.session.gain_x, self.session.gain_y = self._gain[0], self._gain[1]

    def _inside_corridor(
        self, pointer: Pointer, corridor: tuple[tuple[float, float], tuple[float, float]]
    ) -> bool:
        """Whether a detection lies on the segment the motion model predicted."""
        width, height = self.session.frame_size
        x, y = pointer.x * width, pointer.y * height
        (x1, y1), (x2, y2) = corridor
        margin = self._corridor_radius()
        return (
            min(x1, x2) - margin <= x <= max(x1, x2) + margin
            and min(y1, y2) - margin <= y <= max(y1, y2) + margin
        )

    def _corridor_radius(self) -> float:
        """How wide the search corridor is: the pointer plus the prediction's error."""
        return max(24.0, 2.5 * POINTER_RADIUS_PX + 3.0 * self._sigma)

    def _observe(
        self,
        after: bytes | None = None,
        *,
        not_before: float | None = None,
        corridor: tuple[tuple[float, float], tuple[float, float]] | None = None,
        motion: MotionReport | None = None,
    ):
        """Grab one live frame and detect the pointer on it (retrying bounded).

        ``after``/``not_before`` describe the frame/moment before the last move: the
        observation frame must postdate the move. There is no discarded warm-up grab —
        in frame mode every grab is a live frame (~0.9 s), and a second one would look at
        the screen after the AssistiveTouch pointer has already faded.
        """
        width, height = self.session.frame_size
        near = (self.session.position[0] / width, self.session.position[1] / height)
        frame = (
            self._frame_after(after, not_before=not_before)
            if after is not None
            else self.capture.grab_latest()
        )
        for index in range(self.observe_attempts):
            if index > 0:
                frame = self.capture.grab_latest()
            # The full frame is the arbiter: a crop can hide the real pointer behind a
            # decoy that happens to sit inside the corridor. The corridor only says
            # whether the detection agrees with the motion model.
            pointer = self.detector.detect(frame.image, near=near)
            if pointer is not None:
                if corridor is not None and not self._inside_corridor(pointer, corridor):
                    if motion is not None:
                        motion.global_recoveries += 1
            elif corridor is not None:
                # Second chance for a pointer the full frame missed: look only where the
                # model says it should be (counted as a recovery, because the model was
                # wrong about the full-frame result).
                roi = corridor_roi(
                    corridor[0],
                    corridor[1],
                    radius=self._corridor_radius(),
                    frame_size=(width, height),
                )
                pointer = detect_in_roi(self.detector, frame.image, roi, near=near)
                if pointer is not None and motion is not None:
                    motion.global_recoveries += 1
            if pointer is not None:
                return frame, pointer
        if self.tracker is not None:
            later, found = self._observe_by_diff(frame)
            if found is not None:
                return later, found
            frame = later
        return frame, None

    def _travel_on_belief(self, x: float, y: float) -> None:
        """Drive the pointer to the target using only the calibrated belief.

        The AssistiveTouch pointer fades within a second, and a UVC grab is slow enough
        that a camera-in-the-loop correction often reads frames that no longer show it
        (measured live 2026-10-21: the loop then chases its own tail and never clicks).
        Relative moves are exactly predictable, so travel is dead reckoning; the frame is
        used to *verify* the arrival, which is the part that must not be blind.
        """
        # One estimate for both planning and the belief's own integration; a different
        # pair makes the planned delta and the belief drift apart (measured Task 1).
        self.session.gain_x, self.session.gain_y = self._gain[0], self._gain[1]
        for _step in range(self.max_steps):
            error_x = x - self.session.position[0]
            error_y = y - self.session.position[1]
            if math.hypot(error_x, error_y) <= self.tolerance_px:
                return
            # Travel and correction must use the same estimate: the configured gain is
            # only a starting guess (this rig measures 1.63–1.82 against a 1.48 config),
            # so a first leg on `session.gain` undershoots or overshoots every time the
            # rig disagrees with the config.
            dx = max(-self.max_step, min(self.max_step, round(error_x / self._gain[0])))
            dy = max(-self.max_step, min(self.max_step, round(error_y / self._gain[1])))
            if dx == 0 and dy == 0:
                return
            self.session.move(dx, dy)

    def travel(self, x: float, y: float) -> float:
        """Move the pointer to a frame-pixel target without clicking.

        Used where a click is not the gesture (a drag, a hover, a skill positioning the
        pointer before its own button presses). Returns the remaining error in frame
        pixels; the caller decides whether that is close enough.
        """
        if not self.session.connected:
            self.session.connect()
        self._travel_on_belief(x, y)
        return math.hypot(x - self.session.position[0], y - self.session.position[1])

    def travel_verified(self, x: float, y: float) -> TapResult:
        """Move the pointer to the target and verify it, without clicking.

        The same phased loop as :meth:`tap` minus the click: the move bench and any
        caller that needs a positioned pointer (a drag) use it before their own gesture.
        """
        if not self.session.connected:
            self.session.connect()
        if self.capture is not None and self.detector is not None:
            return self._verified_tap(x, y, click=False)
        self._travel_on_belief(x, y)
        return TapResult(True, 1, None, None, "moved by pointer state; not verified")

    def _advance(self, phase: Phase, units_x: int, units_y: int) -> float:
        """Send one move with the belief integrating on that phase's own gain.

        Planning uses the per-phase gain; the belief that predicts where the pointer will
        be must use the same number, or ``prediction_error_px`` and the corridor are
        biased by the difference between the phase gain and the base estimate.
        """
        self.session.gain_x, self.session.gain_y = self.motion_calibration.gain(phase)
        started = time.monotonic()
        self.session.move(units_x, units_y)
        return started

    def _plan_segment(
        self, x: float, y: float, position: tuple[float, float]
    ) -> tuple[Phase, int, int]:
        """One planned segment: its phase and the HID units that serve it.

        The phase sizes the step against the target's effective width; the phase's own
        gain converts frame pixels to units; the phase's cap keeps a segment short
        enough that its acceleration stays in the band that gain was learned at.
        """
        error_x = x - position[0]
        error_y = y - position[1]
        distance = math.hypot(error_x, error_y)
        phase = choose_phase(distance, self.motion_config)
        if distance <= 0.0:
            return phase, 0, 0
        wanted = max(
            self.motion_config.min_segment_px,
            distance * self.motion_config.ratios[phase],
        )
        scale = wanted / distance
        units_x, units_y = self.motion_calibration.units(phase, error_x * scale, error_y * scale)
        cap = self.motion_config.max_units[phase]
        return phase, max(-cap, min(cap, units_x)), max(-cap, min(cap, units_y))

    def _verified_tap(self, x: float, y: float, *, click: bool = True) -> TapResult:
        width, height = self.session.frame_size
        last_pointer: Pointer | None = None
        last_error: float | None = None
        motion = MotionReport()

        # A reference frame from before the travel: each observation must postdate the
        # move it verifies (the pointer is drawn for only a moment, so a queued frame is
        # useless for this).
        previous_frame = self.capture.grab_latest()
        moved_at: float | None = None
        # The first leg is planned like every other one. A full dead-reckoned travel lands
        # on the frame edge when the configured gain underestimates the rig (1.48 vs
        # 1.6–1.8 measured), and a pointer clipped by the edge is undetectable — the
        # detectors then latch onto coloured UI elements instead (rig bench, 2026-11-03).
        self.session.gain_x, self.session.gain_y = self._gain[0], self._gain[1]
        first_phase, first_x, first_y = self._plan_segment(x, y, self.session.position)
        #: The belief's position before the first leg; the first observation's
        #: displacement (relative to it) calibrates the ballistic phase too.
        first_start = self.session.position
        last_position: tuple[float, float] | None = None
        last_move: tuple[int, int] | None = None
        last_phase: Phase | None = None
        if first_x or first_y:
            moved_at = self._advance(first_phase, first_x, first_y)
            motion.segments += 1
            motion.phases.append(first_phase.value)
            last_position = first_start
            last_move, last_phase = (first_x, first_y), first_phase
        #: Where the belief says the pointer is after the last segment; the next
        #: observation's distance to it is the motion model's prediction error.
        last_prediction: tuple[float, float] | None = None
        wakes = 0
        #: A wake nudges the pointer outside the plan; the displacement it contributes must
        #: not be attributed to the last planned segment when the next observation lands.
        wake_since_observation = False

        for attempt in range(1, self.max_attempts + 1):
            for _step in range(self.max_steps):
                corridor = None
                if last_position is not None and last_prediction is not None:
                    corridor = (last_position, last_prediction)
                frame, pointer = self._observe(
                    after=previous_frame.image,
                    not_before=moved_at,
                    corridor=corridor,
                    motion=motion,
                )
                if pointer is None and wakes < self.max_wakes:
                    # The pointer fades within ~1 s of the last move, so a *second*
                    # observation after a correction segment is exactly when it has gone.
                    # Wake it with a deliberate move (bounded) instead of only on the
                    # first step; a pointer that stays gone still ends in pointer_missing.
                    wakes += 1
                    # Redraw the faded pointer in place: a one-way nudge leaves it ~100 px
                    # away, so the next segment spends itself undoing the wake (one extra
                    # observation per wake on the rig). A symmetric pair cancels out.
                    moved_at = time.monotonic()
                    self.session.move(self.wake_step, self.wake_step)
                    self.session.move(-self.wake_step, -self.wake_step)
                    wake_since_observation = True
                    frame, pointer = self._observe(
                        after=frame.image, not_before=moved_at, corridor=corridor, motion=motion
                    )
                if pointer is None:
                    return TapResult(
                        False,
                        attempt,
                        None,
                        None,
                        "pointer not detected before click; refusing to click",
                        "pointer_missing",
                        motion=motion,
                    )
                motion.observations += 1
                previous_frame = frame
                moved_at = None
                frame_age = self._frame_age_s(frame)
                stale = self._stale_reason(frame)
                if stale is not None:
                    return TapResult(
                        False,
                        attempt,
                        pointer,
                        None,
                        f"{stale}; refusing to click on a frame we cannot trust",
                        "stale_frame",
                        frame_age,
                        motion=motion,
                    )
                last_pointer = pointer
                position = (pointer.x * width, pointer.y * height)
                if last_prediction is not None:
                    motion.prediction_error_px = math.hypot(
                        position[0] - last_prediction[0], position[1] - last_prediction[1]
                    )
                    # Size the next corridor by the model's actual error on this rig.
                    self._sigma = min(
                        200.0, max(8.0, 0.6 * self._sigma + 0.4 * motion.prediction_error_px)
                    )
                if (
                    last_position is not None
                    and last_move is not None
                    and not wake_since_observation
                ):
                    observed = (position[0] - last_position[0], position[1] - last_position[1])
                    # The base estimate feeds the belief's own integration; the phase
                    # calibration feeds the next segment's plan. Both learn from this
                    # observation — but only when no wake moved the pointer in between:
                    # its 60 units would be misattributed to the planned segment and learn
                    # a wrong gain (review, 2026-11-03).
                    big_enough = max(abs(last_move[0]), abs(last_move[1])) >= (
                        self.motion_config.min_units_for_calibration
                    )
                    if big_enough:
                        self._update_gain(last_move, observed)
                        if last_phase is not None and self.adaptive_gain:
                            self.motion_calibration.update(last_phase, last_move, observed)
                self.session.observe(*position)
                # One definition of "the pointer is on the target", shared with the
                # driver's read-only precheck (artemis.safety.pointer).
                verdict = check_point(
                    x,
                    y,
                    self.session.position[0],
                    self.session.position[1],
                    tolerance_px=self.tolerance_px,
                )
                error = verdict.error_px if verdict.error_px is not None else 0.0
                last_error = error
                if verdict.ok:
                    if not click:
                        return TapResult(
                            True,
                            attempt,
                            pointer,
                            error,
                            "pointer verified at target; not clicked",
                            None,
                            frame_age,
                            motion=motion,
                        )
                    return self._click_and_verify(
                        x, y, pointer, error, frame, frame_age, attempt, motion
                    )
                phase, units_x, units_y = self._plan_segment(x, y, position)
                if (units_x == 0 and units_y == 0) or (
                    motion.segments >= self.motion_config.max_segments
                ):
                    break
                moved_at = self._advance(phase, units_x, units_y)
                motion.segments += 1
                motion.phases.append(phase.value)
                last_position, last_move, last_phase = position, (units_x, units_y), phase
                last_prediction = self.session.position
                wake_since_observation = False
        return TapResult(
            False,
            self.max_attempts,
            last_pointer,
            last_error,
            "pointer did not converge to the target; refusing to click",
            "pointer_not_centered",
            motion=motion,
        )

    def _click_and_verify(
        self, x, y, pointer, error, frame, frame_age, attempt, motion=None
    ) -> TapResult:
        """Click a verified pointer and demand that the screen reacts."""
        width, height = self.session.frame_size
        before = frame.image
        self.session.click()
        after = self.capture.grab_latest()
        # The target crop decides first; a change elsewhere on the screen is still proof
        # the click landed (a sheet can open away from the target).
        effect = verify_effect(before, after.image, (pointer.x * width, pointer.y * height))
        if effect.ok:
            return TapResult(
                True,
                attempt,
                pointer,
                error,
                "pointer verified at target; click changed the screen",
                None,
                frame_age,
                effect.crop_change,
                motion,
            )
        return TapResult(
            False,
            attempt,
            pointer,
            error,
            "pointer verified at target, but click did not change the screen",
            "no_effect",
            frame_age,
            effect.crop_change,
            motion,
        )

    def tap(self, x: float, y: float) -> TapResult:
        if not self.session.connected:
            self.session.connect()
        if self.capture is not None and self.detector is not None:
            return self._verified_tap(x, y)
        # Legacy dead-reckoning path; no pre-click verification is possible here.
        for attempt in range(1, self.max_attempts + 1):
            before = self.capture.grab_latest() if self.capture is not None else None
            deltas = self.session.tap(x, y)
            if self.capture is None:
                return TapResult(
                    True,
                    attempt,
                    None,
                    0.0,
                    f"clicked by pointer state (delta={deltas}); no screen verification",
                )
            after = self.capture.grab_latest()
            if changed(before.image, after.image):
                return TapResult(
                    True,
                    attempt,
                    None,
                    0.0,
                    f"clicked by pointer state (delta={deltas}); screen changed",
                )
            self.session.ensure_home_screen()
            self.session.home()
        return TapResult(
            False,
            self.max_attempts,
            None,
            None,
            "click did not change the screen; pointer was re-anchored",
            "no_effect",
        )


class MouseController:
    """Turn ``tap(x, y)`` into the smallest reliable sequence of relative moves."""

    def __init__(
        self,
        control,
        capture,
        tracker,
        *,
        frame_size: tuple[int, int] = (449, 974),
        gain: float = 1.3,
        tolerance_px: float = 8.0,
        max_attempts: int = 3,
        nudge: int = 60,
        max_step: int = 300,
        min_confidence: float = 0.5,
        settle_s: float = 0.04,
    ):
        if gain <= 0:
            raise ValueError("gain must be positive")
        self.control = control
        self.capture = capture
        self.tracker = tracker
        self.frame_size = (int(frame_size[0]), int(frame_size[1]))
        self.gain = float(gain)
        self.tolerance_px = float(tolerance_px)
        self.max_attempts = int(max_attempts)
        self.nudge = int(nudge)
        self.max_step = int(max_step)
        self.min_confidence = float(min_confidence)
        self.settle_s = float(settle_s)

    def tap(self, x: float, y: float) -> TapResult:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"target must be normalized 0..1, got ({x}, {y})")

        self.control.preflight()
        target = (float(x), float(y))
        expected = (0.5, 0.5)
        last_pointer: Pointer | None = None
        last_error: float | None = None

        for attempt in range(1, self.max_attempts + 1):
            pointer = self._locate(expected)
            last_pointer = pointer
            if pointer is None or pointer.confidence < self.min_confidence:
                self.control.mouse_home()
                expected = (0.5, 0.5)
                continue

            error_x = (target[0] - pointer.x) * self.frame_size[0]
            error_y = (target[1] - pointer.y) * self.frame_size[1]
            last_error = math.hypot(error_x, error_y)
            if max(abs(error_x), abs(error_y)) <= self.tolerance_px:
                before = self.capture.grab_latest()
                self.control.mouse_click()
                after = self.capture.grab_latest()
                if changed(before.image, after.image):
                    return TapResult(
                        True,
                        attempt,
                        pointer,
                        last_error,
                        "clicked; screen changed",
                    )
                continue

            dx = max(-self.max_step, min(self.max_step, round(error_x / self.gain)))
            dy = max(-self.max_step, min(self.max_step, round(error_y / self.gain)))
            if dx == 0 and dy == 0:
                continue
            self.control.mouse_move(dx, dy)
            expected = (
                pointer.x + dx * self.gain / self.frame_size[0],
                pointer.y + dy * self.gain / self.frame_size[1],
            )

        return TapResult(
            False,
            self.max_attempts,
            last_pointer,
            last_error,
            "not converged or click did not change the screen",
            "no_effect",
        )

    def _locate(self, expected: tuple[float, float]) -> Pointer | None:
        before = self.capture.grab_latest()
        if getattr(self.tracker, "has_template", False):
            detected = self.tracker.detect(before.image)
            if detected is not None:
                return detected

        self.control.mouse_move(self.nudge, 0)
        try:
            if self.settle_s > 0:
                time.sleep(self.settle_s)
            after = self.capture.grab_latest()
            return self.tracker.locate(
                before.image,
                after.image,
                near=expected,
                shift=(self.nudge * self.gain) / self.frame_size[0],
                reach=(0.5, 0.5),
            )
        finally:
            self.control.mouse_move(-self.nudge, 0)


__all__ = ["MouseController", "PointerSession", "SessionTapController", "TapResult"]
