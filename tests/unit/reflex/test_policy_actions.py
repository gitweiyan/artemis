"""Playing by actions: one decision, one tap, and a plan of our own when the model is away.

The placement space needs a turn and a shift spread over frames; the action space is the
four buttons themselves, so a decision is one gesture. What is tested here is that the
policy asks the right question, executes what it answers, and — when the API cannot be
reached — falls back to *our own* plan rather than to nothing (the runtime counts a
decision with no gesture as a wasted frame, and there are only about two usable frames per
piece on this device).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from artemis.reflex.contract import Action
from artemis.reflex.registry import load_skill
from artemis.services import systemone

SKILLS = Path(__file__).resolve().parents[3] / "skills"
EMPTY = ["0" * 10] * 19 + ["1111110000"]
CALIBRATION = {
    "jev": {"mode": "choice"},
    "screen": {"width": 720, "height": 1600},
    "gestures": {
        "rotate": {"kind": "tap", "at_ratio": [0.81, 0.84]},
        "move_left": {"kind": "tap", "at_ratio": [0.13, 0.84]},
        "move_right": {"kind": "tap", "at_ratio": [0.53, 0.84]},
        "drop": {"kind": "tap", "at_ratio": [0.33, 0.84], "enabled": True},
    },
    "weights": {"lines": 1.6, "holes": -6.0, "landing_height": -0.6, "bumpiness": -0.25},
}


@pytest.fixture(scope="module")
def actions():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.policy_actions")


@pytest.fixture(scope="module")
def control():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.control")


def state(**overrides):
    base = {
        "board_matrix": EMPTY,
        "current_piece_mask": [[1, 1, 1, 1]],
        "current_piece_positions": [[3, 0], [3, 1], [3, 2], [3, 3]],
        "motion_vector": [1, 0],
        "counters": {},
    }
    base.update(overrides)
    return base


def answering(monkeypatch, winner):
    def fake_choice(payload, instructions, criteria, **kwargs):
        assert winner in criteria, "the policy must only answer with an offered action"
        return winner, {name: 1.0 if name == winner else 0.0 for name in criteria}

    monkeypatch.setattr(systemone, "choice", fake_choice)


def test_the_policy_asks_for_one_action_and_it_becomes_one_tap(actions, control, monkeypatch):
    answering(monkeypatch, "right")
    actions.reset_telemetry()

    action = actions.decide(state(), CALIBRATION)

    assert action.state["action"] == "right"
    assert action.state["counters"]["source"] == "jev"
    assert actions.telemetry()["calls"] == 1
    gestures = control.to_gestures(action)
    assert [g.description for g in gestures] == ["move right"]
    assert len(gestures) == 1, "one decision, one tap"


def test_the_question_offers_only_actions_the_piece_can_take(actions, monkeypatch):
    """A wall blocks `left`: it must not be on the menu, so the model cannot pick it."""
    asked = {}

    def fake_choice(payload, instructions, criteria, **kwargs):
        asked["criteria"] = criteria
        name = next(iter(criteria))
        return name, {n: 1.0 for n in criteria}

    monkeypatch.setattr(systemone, "choice", fake_choice)
    actions.reset_telemetry()

    actions.decide(state(current_piece_positions=[[3, 0], [3, 1], [3, 2], [3, 3]]), CALIBRATION)

    assert "left" not in asked["criteria"], "the piece is against the left wall"
    assert {"right", "rotate", "drop"} <= set(asked["criteria"])
    assert all(isinstance(value, dict) for value in asked["criteria"].values()), (
        "each option carries what it does and what it leads to"
    )


def test_an_unreachable_api_falls_back_to_our_own_plan(actions, monkeypatch):
    """Not to nothing: a decision with no gesture is a wasted frame, and frames are scarce."""

    def broken(*_args, **_kwargs):
        raise systemone.SystemOneUnavailable("no key")

    monkeypatch.setattr(systemone, "choice", broken)
    actions.reset_telemetry()

    action = actions.decide(state(), CALIBRATION)

    assert action.state["action"] in {"left", "right", "rotate", "drop"}
    assert action.state["counters"]["source"] == "plan", "our plan played, and it says so"
    assert actions.telemetry()["fallbacks"] == 1
    assert actions.telemetry()["last_error"]


def test_the_session_ends_on_a_still_board_like_the_other_policies(actions):
    stopped = actions.decide(
        state(stall={"stalled": True, "static_s": 6.0, "similarity": 0.999}),
        CALIBRATION,
    )
    assert stopped.done is True
    assert stopped.state["note"] == "board static"


def test_a_frame_without_a_piece_is_left_alone(actions):
    action = actions.decide(state(current_piece_mask=[], current_piece_positions=[]), CALIBRATION)
    assert action.done is False
    assert action.gestures == ()


def test_the_dispatcher_switches_space_from_the_calibration(actions, monkeypatch):
    """One calibration line decides between the two option spaces."""
    import importlib as _importlib

    dispatch = _importlib.import_module("artemis_skills_tetris.policy_dispatch")
    answering(monkeypatch, "rotate")

    chosen = dispatch.decide(
        state(), {**CALIBRATION, "jev": {"mode": "choice", "option_space": "action"}}
    )

    assert chosen.state["action"] == "rotate", "the action space ran"
    assert chosen.state["counters"]["source"] == "jev"

    # And the placement space is still what runs by default.
    from artemis.reflex.contract import Action as _Action

    assert isinstance(dispatch.decide(state(), CALIBRATION), _Action)
