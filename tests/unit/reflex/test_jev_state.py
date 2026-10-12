"""T6: the exact message Jev is asked about — one place, one key set.

Jev takes text, not images, so this dict *is* the interface between computer vision
and the decision: whatever is missing here is something the model cannot see, and
whatever is extra here is something nobody agreed on. Hence the tests pin the key set
exactly rather than "contains".

Three properties the rest of the chain depends on:

* the board travels as text rows, top to bottom (what a reader would see);
* the piece travels as **geometry** — a mask and its positions — never as a letter,
  because a game with custom shapes has no letters to name them (Review Focus 4);
* every candidate move carries an id, which is how a typed answer maps back to a
  move. An id that does not round-trip is a policy that plays a move nobody judged.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from artemis.reflex.registry import load_skill

SKILLS = Path(__file__).resolve().parents[3] / "skills"
KEYS = {
    "board_matrix",
    "current_piece_mask",
    "current_piece_positions",
    "motion_vector",
    "mechanics",
    "next_preview",
    "candidate_moves",
}


@pytest.fixture(scope="module")
def jev_state():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.jev_state")


@pytest.fixture(scope="module")
def simulator():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.simulator")


BOARD = ["0" * 10 for _ in range(19)] + ["1110000000"]
PIECES = {
    "board_matrix": ["0" * 10 for _ in range(20)],
    "current_piece_positions": [[16, 3], [16, 4], [17, 3]],
    "current_piece_mask": [[1, 1], [1, 0]],
    "motion_vector": [1, 0],
}


def placements():
    """Three placements in the shape ``legal_placements`` returns them."""
    simulator = importlib.import_module("artemis_skills_tetris.simulator")
    return [
        (simulator.Placement(rotation=0, column=0), None),
        (simulator.Placement(rotation=0, column=1), None),
        (simulator.Placement(rotation=1, column=4), None),
    ]


def build(jev_state, **overrides):
    arguments = {
        "board_matrix": BOARD,
        "pieces": PIECES,
        "mechanics": {"gravity_direction": "down", "piece_source": "vision"},
        "next_preview": [[1, 1, 1, 1]],
        "candidates": placements(),
    }
    arguments.update(overrides)
    return jev_state.build_state(**arguments)


def test_the_message_has_exactly_the_keys_jev_is_told_about(jev_state):
    assert set(build(jev_state)) == KEYS


def test_the_board_travels_as_text_rows_top_to_bottom(jev_state):
    message = build(jev_state)

    assert message["board_matrix"] == BOARD
    assert len(message["board_matrix"]) == 20
    assert all(len(row) == 10 for row in message["board_matrix"])
    assert set("".join(message["board_matrix"])) <= {"0", "1"}


def test_the_piece_travels_as_geometry_never_as_a_letter(jev_state):
    message = build(jev_state)

    assert message["current_piece_mask"] == [[1, 1], [1, 0]]
    assert message["current_piece_positions"] == [[16, 3], [16, 4], [17, 3]]
    with pytest.raises(ValueError, match="mask"):
        build(jev_state, next_preview="T")


def test_every_candidate_move_gets_an_id_an_answer_can_name(jev_state):
    message = build(jev_state)

    assert [move["id"] for move in message["candidate_moves"]] == ["r0c0", "r0c1", "r1c4"]
    assert [move["rotation"] for move in message["candidate_moves"]] == [0, 0, 1]
    assert [move["column"] for move in message["candidate_moves"]] == [0, 1, 4]
    assert set(message["candidate_moves"][0]) == {"id", "rotation", "column"}


def test_the_mechanics_block_carries_the_gravity_and_the_piece_source(jev_state):
    """With no measured motion, what the calibration declares is what Jev is told."""
    message = build(
        jev_state,
        pieces=dict(PIECES, motion_vector=None),
        mechanics={"gravity_direction": "left", "piece_source": "pixels"},
    )

    assert message["mechanics"] == {"gravity_direction": "left", "piece_source": "pixels"}


@pytest.mark.parametrize(
    ("motion", "expected"),
    [([1, 0], "down"), ([-1, 0], "up"), ([0, -1], "left"), ([0, 1], "right")],
)
def test_the_observed_motion_sets_the_gravity_direction(jev_state, motion, expected):
    """Review Focus 3: when the game switches gravity, the measurement says so.

    A declared direction is an assumption; the piece's own displacement is evidence.
    """
    pieces = dict(PIECES, motion_vector=motion)

    message = build(jev_state, pieces=pieces)

    assert message["mechanics"]["gravity_direction"] == expected
    assert message["motion_vector"] == motion


def test_without_motion_the_declared_gravity_stands(jev_state):
    """A piece that has not moved yet says nothing about gravity."""
    message = build(jev_state, pieces=dict(PIECES, motion_vector=None))

    assert message["mechanics"]["gravity_direction"] == "down"
    assert message["motion_vector"] is None

    still = build(jev_state, pieces=dict(PIECES, motion_vector=[0, 0]))
    assert still["mechanics"]["gravity_direction"] == "down"


def test_a_message_without_grammar_errors(jev_state):
    """It is sent as JSON: anything unserialisable is a failed call, not a bad answer."""
    payload = json.dumps(build(jev_state))

    assert json.loads(payload)["candidate_moves"][1]["id"] == "r0c1"


def test_a_message_with_no_piece_says_so_instead_of_inventing_one(jev_state):
    message = build(jev_state, pieces={}, next_preview=None, candidates=[])

    assert message["current_piece_mask"] == []
    assert message["current_piece_positions"] == []
    assert message["motion_vector"] is None
    assert message["candidate_moves"] == []
    assert message["next_preview"] is None
