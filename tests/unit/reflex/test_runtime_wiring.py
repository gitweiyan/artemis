"""The loop's wiring, not its parts: does a decision actually reach the device?

Every other test in this directory calls the skill the way the *tests* like to call it —
``to_gestures(action, calibration, state=state)``, with everything to hand. The runtime
does not: it calls ``skill.decide(state, skill.calibration)`` and then
``skill.to_gestures(action)`` with the action **alone** (see ``ReflexRuntime.run``), which
is why ``control.to_gestures`` documents that "the calibration is attached to the action's
state by the policy".

It wasn't. On the first live run the loop decided correctly frame after frame — the trace
shows ``phase: "move"``, ``move_columns: -1``, Jev answering in the note — and dispatched
**zero** gestures: ``actions: 0`` after 16 s of play, with the piece never moved. Every
unit test passed, because every unit test handed the calibration over itself.

So this file drives the real runtime and asks the only question that matters at the seam:
did anything come out the other end? The decisions are injected, because the point here is
the wiring, not what the search policy would have chosen.
"""

from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path

import pytest

from artemis.reflex.contract import Action, Frame
from artemis.reflex.registry import load_skill
from artemis.reflex.runtime import run_reflex

SKILLS = Path(__file__).resolve().parents[3] / "skills"
SAMPLES = SKILLS / "tetris" / "samples"
#: Two real frames that differ by a falling piece, so ``parse`` has a board and a piece.
FRAMES = ("session-0001.png", "session-0002.png")


class ReplayCapture:
    """Hands the runtime the same real frames, over and over."""

    def __init__(self, names=FRAMES):
        self.images = [(SAMPLES / name).read_bytes() for name in names]
        self.index = 0
        self.closed = False

    def grab(self) -> Frame:
        image = self.images[self.index % len(self.images)]
        self.index += 1
        return Frame(image=image, captured_at=0.0, capture_ms=1.0, width=720, height=1600)

    def close(self) -> None:
        self.closed = True


class RecordingControl:
    """Remembers every gesture the runtime dispatched."""

    def __init__(self):
        self.gestures = []

    def dispatch(self, gesture) -> float:
        self.gestures.append(gesture)
        return 1.0


@pytest.fixture(scope="module")
def offline_skill():
    """The skill with the offline backend: no model call while testing the wiring."""
    handle = load_skill("tetris", directory=SKILLS)
    return replace(handle, calibration={**handle.calibration, "policy_backend": "search"})


@pytest.fixture
def move_two_columns(monkeypatch):
    """A decision that cannot be a no-op: the piece must move two columns right.

    Injected rather than discovered, because what is under test is whether a decision
    reaches the device — not which column the search policy likes on those frames (and the
    committed pair happens to hold a three-cell shape no letter covers, so the search
    policy declines to decide at all).
    """
    policy = importlib.import_module("artemis_skills_tetris.policy")
    wanted = Action(
        state={
            "phase": "move",
            "move_columns": 2,
            "placement": {"rotation": 0, "column": 5},
            "current_column": 3,
            "counters": {},
        }
    )
    monkeypatch.setattr(policy, "decide", lambda _state, _calibration: wanted)
    return wanted


def run_once(handle, seconds: float = 0.6, fps_cap: float = 20.0):
    """A short session: the frames are replayed from memory, so the budget is the only cost."""
    capture, control = ReplayCapture(), RecordingControl()
    result = run_reflex(handle, capture=capture, control=control, seconds=seconds, fps_cap=fps_cap)
    return result, capture, control


def test_the_runtime_dispatches_the_gestures_the_skill_asks_for(offline_skill, move_two_columns):
    """A decision that never becomes a gesture is a session that plays nothing."""
    result, capture, control = run_once(offline_skill)

    assert result.frames > 1, "the runtime did not even get going"
    assert result.skill_error is None
    assert control.gestures, (
        "the runtime dispatched nothing at all — the policy is not handing the calibration "
        "to `to_gestures`, which receives the action alone"
    )
    assert result.actions == len(control.gestures)
    # The injected decision turns nothing and moves two columns: every frame it is asked
    # for two taps, on the right button, and nothing else.
    assert set(g.description for g in control.gestures) == {"move right"}
    assert len(control.gestures) % 2 == 0
    assert capture.closed, "the runtime must close the capture it was given"


def test_every_gesture_lands_on_a_calibrated_button(offline_skill, move_two_columns):
    """And on the right ones: taps go to the game's own four controls, nothing else."""
    _result, _capture, control = run_once(offline_skill)
    calibration = offline_skill.calibration
    screen = calibration["screen"]
    buttons = {
        name: (
            round(entry["at_ratio"][0] * screen["width"]),
            round(entry["at_ratio"][1] * screen["height"]),
        )
        for name, entry in calibration["gestures"].items()
        if isinstance(entry, dict) and "at_ratio" in entry
    }

    assert control.gestures
    for gesture in control.gestures:
        assert gesture.kind == "tap", f"this game is played with taps, got {gesture.kind}"
        assert gesture.description, "a gesture without a description cannot be reviewed"
        ((x, y),) = gesture.points
        # Within a couple of pixels: the control layer carries the calibrated point as a
        # float, and `adb input tap` truncates it on the way out.
        nearest = min(buttons.values(), key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
        assert abs(nearest[0] - x) <= 2 and abs(nearest[1] - y) <= 2, (
            f"tap ({x}, {y}) is not one of the controls {buttons}"
        )
    assert control.gestures[0].description == "move right", "two columns right"


def test_the_action_carries_the_calibration_the_control_layer_needs(
    offline_skill, move_two_columns
):
    """The seam itself, without the runtime: ``to_gestures(action)`` must not be empty."""
    handle = offline_skill
    state = handle.parse(ReplayCapture().grab())
    assert state, "the committed frames should read as a board"

    action = handle.decide(state, handle.calibration)
    gestures = handle.to_gestures(action)
    assert gestures, (
        "`to_gestures(action)` returned nothing: the calibration did not ride along, and "
        "the runtime has no other way to give it to the control layer"
    )


def test_nothing_is_tapped_on_the_game_over_panel(offline_skill):
    """The end-of-round screen must produce restarts, never play taps."""
    capture, control = ReplayCapture([("panel-gameover.png")]), RecordingControl()
    result = run_reflex(offline_skill, capture=capture, control=control, seconds=5.0, fps_cap=200.0)

    assert control.gestures == []
    assert result.final_state is not None
    assert result.final_state.get("game_over") is True
    assert "stall" in result.final_state
