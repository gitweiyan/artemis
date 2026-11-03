# iOS Move Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the frame grabs and wall-clock time a verified iOS tap needs, and stop failed taps from
costing 60–80 s, by replacing fixed-step dead reckoning with a three-phase closed-loop motion model
(per-phase HID gains) plus corridor-scoped pointer observation.

**Architecture:** A pure `artemis/reflex/motion.py` owns the phase policy and the per-phase
calibration. `SessionTapController` asks it for each segment's HID units, executes them through
`PointerSession`, observes the pointer (corridor ROI first, full frame as recovery), and feeds the
measured displacement back into the calibration. Target recognition (OCR/explorer) and pointer
tracking stay separate: no model call ever moves the mouse.

**Tech Stack:** Python 3.12, pytest, OpenCV/Pillow (already in `artemis/reflex/vision.py`), ESP32 HID
serial (`Esp32HidControl`), UVC capture (`UvcCapture`, frame mode).

**Spec:** `docs/move_plan.md` — the design proposal (V1 closed-loop motion, V2 trajectory ROI, V3
streaming). This plan implements V1 + V2 and explicitly defers V3 (see Non-goals, and the measured
reason below).

## Context the spec does not have (this rig, measured 2026-11-03)

- Capture is **frame mode** (`ARTEMIS_IOS_CAPTURE=frame`): every observation is a fresh ffmpeg grab,
  ≈0.9 s. The card freezes on a persistent AVFoundation stream (playbook §4), so V3 "observe during a
  stroke" is not buildable on this hardware today. **The lever is fewer observations, not faster ones.**
- Pointer gain is not constant: 200-unit moves measured **1.82**, 400-unit moves **1.63**
  (frame px/unit), while `PointerSession` is configured with **1.48**. `_travel_on_belief` uses the
  configured value, so dead-reckoned moves overshoot by 10–23 % — enough to land on the Home-gesture
  band. `_verified_tap` corrections already use the online estimate (`self._gain`), the initial
  travel does not.
- Drags (`mswipe`) measure **≈0.83** frame px/unit — a different gain from moves. Do not reuse a
  move gain for a drag.
- Pointer detection is a **blue ring** (`CompositePointerDetector`, `method="blue-ring"`) with the
  `PointerTracker` two-frame diff as fallback. The spec's red-ring HSV detector is not needed; keep
  the existing detector and add ROI cropping around it.

## Global Constraints

- No click without a pointer detected in a frame captured **after** the last move. Keep the existing
  incidents verbatim: `pointer_missing`, `stale_frame`, `pointer_not_centered`, `no_effect`.
- Controller coordinates are capture-frame pixels (449×974 on this rig); HID units are what the
  firmware takes. Never assume `Δframe == Δunits`; always convert through a calibrated gain.
- Every observation costs ≈0.9 s: a task's tests must assert **observation counts**, not just outcomes.
- `Esp32HidControl.mouse_move` splits into int8 segments (≤127) and waits for the board's `OK`; the
  planner must never emit a single report above 127 units.
- A faded pointer is woken with exactly one 60-unit move before the observation is trusted (existing
  behaviour; do not change).
- `tests/unit` baseline is 14 known failures (admin_console 2, explorer 9, video_analyzer 3); any run
  must not exceed 14.
- Plans and ledgers: progress notes go to `.superpowers/sdd/<plan>/progress.md`.

## Review Focus

- A pointer that is visible but never converges must still end in `pointer_not_centered` **without a
  click** (the retry budget must not turn into a free click).
- A pointer that disappears after the first segment must end in `pointer_missing` without clicking,
  and must never verify a click on a stale frame.
- Per-phase gains must not leak: a ballistic observation must not retune the fine gain.
- The corridor must not make a false detection easier; a missed ROI search must fall back to the full
  frame **before** the pointer is declared missing.
- The first observation of a session (pointer at 0,0 after `connect()`) must not be skipped by a
  "predicted" position — prediction only exists after an observed segment.

---

### Task 1: Travel on the measured gain

**Files:**
- Modify: `artemis/reflex/mouse.py` (`SessionTapController._travel_on_belief`)
- Test: `tests/unit/reflex/test_pointer_session.py`

**Interfaces:**
- Consumes: `self._gain: list[float]` (the online EMA already updated by `_update_gain`).
- Produces: no signature change; `travel()` and `_verified_tap()`'s first leg become accurate.

- [ ] **Step 1: Write the failing test**

```python
def test_travel_uses_the_online_gain_not_the_configured_default():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(session, capture=None)
    controller._gain = [4.0, 4.0]  # what observations have measured on this rig

    remaining = controller.travel(100, 100)

    assert control.moves == [(25, 25)], "100 px at the measured 4.0 px/unit, not the 2.0 default"
    assert remaining <= controller.tolerance_px
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/reflex/test_pointer_session.py::test_travel_uses_the_online_gain_not_the_configured_default -q`
Expected: FAIL — `control.moves == [(50, 50)]`.

- [ ] **Step 3: Implement**

In `_travel_on_belief`, replace `self.session.gain_x` / `gain_y` with `self._gain[0]` / `self._gain[1]`
in the two `round(error / ...)` expressions. Add one comment: corrections and travel must use the
same estimate or the first leg undershoots/overshoots whenever the rig disagrees with the config.

- [ ] **Step 4: Run the file**

Run: `.venv/bin/python -m pytest tests/unit/reflex/test_pointer_session.py -q`
Expected: PASS (the existing `[(50, 50)]` assertions still hold — `_gain` starts from `session.gain`).

- [ ] **Step 5: Commit**

```bash
git add artemis/reflex/mouse.py tests/unit/reflex/test_pointer_session.py
git commit -m "fix(reflex): travel on the measured pointer gain"
```

---

### Task 2: Motion model (phases + per-phase calibration)

**Files:**
- Create: `artemis/reflex/motion.py`
- Test: `tests/unit/reflex/test_motion.py` (new)

**Interfaces:**
- Produces:
  - `Phase(str, Enum)`: `BALLISTIC`, `APPROACH`, `FINE`.
  - `MotionConfig` dataclass: `target_width_px: float = 96.0`, `ballistic_factor: float = 4.0`,
    `approach_factor: float = 1.5`, `ratios: dict[Phase, float]` = 0.75 / 0.55 / 0.30,
    `max_units: dict[Phase, int]` = 300 / 120 / 20, `max_segments: int = 12`,
    `min_segment_px: float = 4.0`.
  - `choose_phase(distance_px: float, config: MotionConfig) -> Phase`.
  - `MotionCalibration(base: tuple[float, float], *, ema: float = 0.4)` with
    `units(phase, dx_px, dy_px) -> tuple[int, int]`, `gain(phase) -> tuple[float, float]`,
    `update(phase, units: tuple[int, int], observed_px: tuple[float, float]) -> None`.
- Consumes: nothing (pure module, no I/O, no session).

- [ ] **Step 1: Write the failing tests**

```python
def test_phase_boundaries_are_multiples_of_the_target_width():
    from artemis.reflex.motion import MotionConfig, Phase, choose_phase

    cfg = MotionConfig(target_width_px=100.0)
    assert choose_phase(500, cfg) is Phase.BALLISTIC   # > 4W
    assert choose_phase(400, cfg) is Phase.APPROACH    # 4W is not ballistic
    assert choose_phase(150, cfg) is Phase.APPROACH    # 1.5W is not fine
    assert choose_phase(149, cfg) is Phase.FINE


def test_units_convert_through_the_phase_gain():
    from artemis.reflex.motion import MotionCalibration, Phase

    cal = MotionCalibration((2.0, 2.0))
    assert cal.units(Phase.BALLISTIC, 100, -50) == (50, -25)


def test_update_retunes_only_the_phase_that_moved():
    from artemis.reflex.motion import MotionCalibration, Phase

    cal = MotionCalibration((2.0, 2.0), ema=0.5)
    cal.update(Phase.FINE, (10, 0), (20.0, 0.0))   # measured 2.0 -> unchanged
    cal.update(Phase.FINE, (10, 0), (40.0, 0.0))   # measured 4.0 -> 3.0 after EMA
    assert cal.gain(Phase.FINE) == pytest.approx((3.0, 2.0))
    assert cal.gain(Phase.BALLISTIC) == pytest.approx((2.0, 2.0))


def test_a_zero_unit_axis_keeps_its_gain_and_gains_stay_in_the_plausible_band():
    from artemis.reflex.motion import MotionCalibration, Phase

    cal = MotionCalibration((2.0, 2.0), ema=1.0)
    cal.update(Phase.FINE, (0, 10), (0.0, 1.0))    # zero-unit axis: no ratio to learn
    assert cal.gain(Phase.FINE)[0] == pytest.approx(2.0)
    for _ in range(50):
        cal.update(Phase.FINE, (10, 10), (10_000.0, 10_000.0))
    assert cal.gain(Phase.FINE) == pytest.approx((4.0, 4.0))  # clamped, not unbounded
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/reflex/test_motion.py -q`
Expected: FAIL — `ModuleNotFoundError: artemis.reflex.motion`.

- [ ] **Step 3: Implement `artemis/reflex/motion.py`**

Rules the body must honour: `choose_phase` compares `distance_px` against
`config.target_width_px * config.ballistic_factor` and `* config.approach_factor`; `units()` rounds
and returns ints; `update()` skips an axis whose units are 0, computes `ratio = observed / units`,
ignores ratios outside `[0.3, 4.0]` (a mis-detection, not a gain), and applies
`gain = ema * ratio + (1 - ema) * old`, clamped to `[0.3, 4.0]`. Every phase starts at `base`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/reflex/test_motion.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artemis/reflex/motion.py tests/unit/reflex/test_motion.py
git commit -m "feat(reflex): add the per-phase pointer motion model"
```

---

### Task 3: Plan segments, not fixed steps

**Files:**
- Modify: `artemis/reflex/mouse.py` (`SessionTapController.__init__`, `_travel_on_belief`,
  `_verified_tap`, `TapResult`)
- Test: `tests/unit/reflex/test_pointer_session.py` (extend; update the two exact-delta assertions)

**Interfaces:**
- Consumes: `Phase`, `MotionConfig`, `MotionCalibration` from Task 2.
- Produces:
  - `SessionTapController(..., motion_config: MotionConfig | None = None,
    motion_calibration: MotionCalibration | None = None)`; calibration defaults to
    `MotionCalibration((session.gain_x, session.gain_y))` and is stored as
    `self.motion_calibration` (the bench reads it).
  - `TapResult.motion: MotionReport | None`, where
    `MotionReport(segments: int, observations: int, phases: list[str],
    prediction_error_px: float | None = None, global_recoveries: int = 0)`.
  - A private `_plan_segment(x: float, y: float, position: tuple[float, float]) -> tuple[Phase, int, int]`
    returning `(phase, units_x, units_y)`.

- [ ] **Step 1: Write the failing tests**

```python
class _LinearPointer:
    """A control that moves a virtual pointer by units * gain (the rig's model)."""

    def __init__(self, gain=2.0, start=(0.0, 0.0)):
        self.gain = gain
        self.x, self.y = start
        self.moves = []
        self.clicks = 0

    def preflight(self):
        return {"mounted": 1, "suspended": 0, "mouse": 1, "keyboard": 1, "touch": 0}

    def ios_home(self):
        pass

    def mouse_home(self):
        self.x = self.y = 0.0

    def mouse_move(self, dx, dy):
        self.moves.append((dx, dy))
        self.x += dx * self.gain
        self.y += dy * self.gain

    def mouse_click(self):
        self.clicks += 1


def test_a_far_target_converges_in_a_few_segments_and_reports_them():
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    control = _LinearPointer(gain=2.0)
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))] * 20, control=control)
    detector = _PointerAtControl(control, frame_size=(449, 974))
    controller = SessionTapController(
        session, capture=capture, detector=detector, settle_timeout_s=0.0
    )

    result = controller.tap(400, 800)

    assert result.ok is True
    assert control.clicks == 1
    assert 1 <= result.motion.segments <= 4
    assert result.motion.observations <= 4
    assert "ballistic" in result.motion.phases
```

(`_PointerAtControl` is a test helper that returns `Pointer(x/449, y/974, 0.95, "fake")` from the
virtual control's position; add it next to `FakeCapture`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/reflex/test_pointer_session.py::test_a_far_target_converges_in_a_few_segments_and_reports_them -q`
Expected: FAIL — `AttributeError: 'TapResult' object has no attribute 'motion'`.

- [ ] **Step 3: Implement**

In `_verified_tap`, keep the existing observe → stale-check → wake-once → click-and-verify skeleton
unchanged. Replace only the movement: after `session.observe(*position)` compute the remaining
distance, call `_plan_segment`, then `session.move(*units)` and record
`segments`/`observations`/`phases`. The first leg stays `_travel_on_belief(x, y)` (now accurate
after Task 1); the planner owns every leg after the first observation, so the very first observation
of a session still comes from the configured start state, never from a prediction.

`_plan_segment` rules: `phase = choose_phase(distance, config)`;
`wanted = max(config.min_segment_px, distance * config.ratios[phase])`;
`units = calibration.units(phase, dx * wanted / distance, dy * wanted / distance)`;
cap each axis at `config.max_units[phase]`, then at 127 (firmware int8); skip a zero/zero segment.
Feed the measured displacement back with
`calibration.update(phase, units, (position[0] - last_position[0], position[1] - last_position[1]))`.

Update the two tests that pin exact dead-reckoned deltas
(`test_verified_tap_clicks_only_after_pointer_is_at_the_target`,
`test_verified_tap_corrects_an_offset_pointer_before_clicking`): assert convergence and exactly one
click, not the old move tuples. Every `pointer_*` incident test must stay unchanged.

- [ ] **Step 4: Run the file**

Run: `.venv/bin/python -m pytest tests/unit/reflex/test_pointer_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artemis/reflex/mouse.py tests/unit/reflex/test_pointer_session.py
git commit -m "feat(reflex): plan pointer segments by distance phase"
```

---

### Task 4: Observe the corridor, recover globally

**Files:**
- Modify: `artemis/reflex/vision.py` (add `crop_for_roi`, `detect_in_roi`)
- Modify: `artemis/reflex/mouse.py` (`_observe` gains a corridor + recovery counter)
- Test: `tests/unit/reflex/test_vision_roi.py` (new), `tests/unit/reflex/test_pointer_session.py`

**Interfaces:**
- Produces:
  - `corridor_roi(start: tuple[float, float], end: tuple[float, float], *, radius: float,
    frame_size: tuple[int, int]) -> tuple[int, int, int, int]` (pixel box, clipped).
  - `detect_in_roi(detector, image: bytes, roi, *, near: tuple[float, float] | None = None)
    -> Pointer | None` — crops, detects, maps back to full-frame normalized coordinates.
  - `TapResult.motion.global_recoveries: int`.
  - `_observe(self, after: bytes | None = None, corridor: tuple[tuple[float, float],
    tuple[float, float]] | None = None)`; `self._sigma: float` (EMA of the prediction error,
    starting at 8.0) drives the corridor radius.
- Consumes: the existing detector protocol `detect(image, near=None) -> Pointer | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_pointer_inside_the_corridor_is_found_and_mapped_back():
    from artemis.reflex.vision import CompositePointerDetector, corridor_roi, detect_in_roi

    image = _pointer_image((300, 700))  # 449x974 frame with a blue ring at that pixel
    roi = corridor_roi((200, 600), (320, 760), radius=80, frame_size=(449, 974))
    found = detect_in_roi(CompositePointerDetector(), image, roi)

    assert found is not None
    assert abs(found.x * 449 - 300) < 6 and abs(found.y * 974 - 700) < 6


def test_a_pointer_outside_the_corridor_is_missed_locally_and_found_globally():
    from artemis.reflex.vision import CompositePointerDetector, corridor_roi, detect_in_roi

    image = _pointer_image((60, 120))
    roi = corridor_roi((300, 600), (380, 700), radius=40, frame_size=(449, 974))

    assert detect_in_roi(CompositePointerDetector(), image, roi) is None
    assert CompositePointerDetector().detect(image) is not None  # the global recovery
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/reflex/test_vision_roi.py -q`
Expected: FAIL — `ImportError: cannot import name 'corridor_roi'`.

- [ ] **Step 3: Implement**

`corridor_roi` pads the segment's bounding box by `radius` and clamps to the frame.
`detect_in_roi` opens the image with Pillow, crops, calls `detector.detect(crop, near=...)` with
`near` re-expressed in crop-normalized coordinates, and adds the crop offset back before normalizing.
Radius in `_observe`: `max(24.0, 2.5 * 20.0 + 3 * sigma)` where `sigma` is the EMA of the last
prediction error (start 8.0). `_observe` tries the corridor first; on `None` it calls
`detector.detect(frame.image, near=...)` on the full frame and increments
`motion.global_recoveries`. The tracker diff fallback stays as the last resort, unchanged.

- [ ] **Step 4: Run both files**

Run: `.venv/bin/python -m pytest tests/unit/reflex/test_vision_roi.py tests/unit/reflex/test_pointer_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artemis/reflex/vision.py artemis/reflex/mouse.py tests/unit/reflex/test_vision_roi.py tests/unit/reflex/test_pointer_session.py
git commit -m "feat(reflex): observe the pointer in the motion corridor first"
```

---

### Task 5: Surface the numbers and benchmark the rig

**Files:**
- Modify: `artemis/control/hid.py` (`_metrics` flattens `motion` into the action metrics)
- Create: `scripts/ios_move_bench.py`
- Modify: `docs/ios-rig-playbook.md` (§3 movement model + how to read the metrics)
- Test: `tests/unit/control/test_hid_sink.py`

**Interfaces:**
- Produces: `ActionResult.metrics["motion_segments"]`, `["motion_observations"]`,
  `["motion_phases"]` (comma-joined), `["motion_prediction_error_px"]`,
  `["motion_global_recoveries"]`.
- Produces: `scripts/ios_move_bench.py --target X,Y --runs N [--port ...]` — travel only (never
  clicks), prints one JSON line per run (`final_error_px`, `segments`, `observations`, `phases`,
  `global_recoveries`) and a summary; exits 1 if the hit rate (final error ≤ 8 px) is below 90 % or
  the median observations exceed 4.

- [ ] **Step 1: Write the failing test**

```python
def test_failed_and_successful_taps_expose_the_motion_counts():
    pointer = SimpleNamespace(x=0.5, y=0.25, confidence=0.9)
    motion = SimpleNamespace(segments=3, observations=2, phases=["ballistic", "approach"],
                             prediction_error_px=6.5, global_recoveries=0)
    controller = FakeController(
        TapResult(True, 1, pointer, 3.0, "ok", motion=motion)
    )
    sink = HidControlSink(FakeSession(), controller)

    result = asyncio.run(sink.tap_abs(540, 1200))

    assert result.metrics["motion_segments"] == 3
    assert result.metrics["motion_observations"] == 2
    assert result.metrics["motion_phases"] == "ballistic,approach"
    assert result.metrics["motion_prediction_error_px"] == 6.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/control/test_hid_sink.py::test_failed_and_successful_taps_expose_the_motion_counts -q`
Expected: FAIL — `motion` is not a `TapResult` field yet / the key is missing.

- [ ] **Step 3: Implement**

`_metrics` reads `getattr(result, "motion", None)` and flattens each field into the metrics dict
(strings for `phases`, floats for the counts). `ios_move_bench.py` reuses
`build_ios_uvc_session` + `IOSUvcHidDriver`: it connects the session, calls `controller.travel(x, y)`
for the target (no click), then grabs one frame and runs the session's detector to measure the *real*
remaining error — the same read-only pattern as `scripts/ios_pointer_servo.py`. It prints the learned
per-phase gains (`controller.motion_calibration`) alongside each run so a failing bench shows whether
the gain or the planner is off. Playbook §3 gains five
lines: phases, per-phase gains, corridor search, recovery, and the two benchmark commands.

- [ ] **Step 4: Run the unit suite and the rig bench**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: `14 failed, N passed` — the same 14 known failures, no new ones.

Run on the rig (with the phone unlocked and the pointer visible):
`ARTEMIS_ESP32_PORT=/dev/cu.usbmodem5B140947461 .venv/bin/python scripts/ios_move_bench.py --target 224,811 --runs 10`
Expected: hit rate ≥ 90 %, median observations ≤ 4, median final error ≤ 8 px. Record the numbers in
the commit message.

- [ ] **Step 5: Commit**

```bash
git add artemis/control/hid.py scripts/ios_move_bench.py docs/ios-rig-playbook.md tests/unit/control/test_hid_sink.py
git commit -m "feat: expose iOS motion metrics and add the move bench"
```

---

## Non-goals (deliberately not in this plan)

- **V3 streaming observation.** The capture card freezes on a persistent stream (playbook §4), so
  "observe during a stroke" cannot be validated here. Revisit if the card or the capture mode changes.
- **Porting the spec's red-ring HSV detector.** The rig's pointer is a blue ring and
  `CompositePointerDetector` already finds it (`method="blue-ring"`); this plan only adds ROI cropping.
- **Plumbing the target's real bounding box into the planner.** `HidControlSink.tap_abs` receives a
  point, not a box, so the phase width is the configured `target_width_px`. Passing the element box
  (executor → sink → controller) is a separate, smaller plan once the phase model is proven.
- **Changing the drag (`mswipe`) model.** Its gain (≈0.83 px/unit) differs from moves; the swipe path
  was already reworked (anchor + keyboard-first) and is out of scope here.

## Acceptance summary

1. `tests/unit` stays at the 14 known failures, with the new motion/ROI tests green.
2. The rig bench meets: hit rate ≥ 90 %, median observations ≤ 4 per travel, median final error ≤ 8 px.
3. `artemis ios-smoke --target <visible>` stays four-section green (snapshot, pointer_motion, locate,
   tap) — the tap section is the end-to-end proof that the new planner still clicks only on a
   verified pointer.
