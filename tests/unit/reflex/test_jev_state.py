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
    assert set(build(jev_state)) == KEYS | {
        "task",
        "goal",
        "execution",
        "encoding",
        "piece_orientations",
    }


def test_the_message_tells_jev_what_the_task_is(jev_state):
    """Measured: the first real call was flat (0.34-0.37 over 18 candidates) and the
    message never said what game it was, what the goal was, or how to read the numbers."""
    message = build(jev_state)

    assert "10x20" in message["task"] and "turn" in message["task"]
    assert "lines" in message["goal"] and "holes" in message["goal"]
    assert "column" in message["execution"]
    for key in (
        "board_matrix",
        "current_piece_mask",
        "current_piece_positions",
        "piece_orientations",
        "motion_vector",
        "candidate_moves",
        "next_preview",
    ):
        assert key in message["encoding"], key
    assert "rotation is an index into piece_orientations" in message["encoding"]["candidate_moves"]


def test_the_shapes_are_given_instead_of_a_rotation_direction(jev_state):
    """The game's rotate control has never been checked for direction, so the payload
    must not make Jev depend on it: it gets the shape of every orientation instead."""
    message = build(jev_state)
    text = json.dumps(message, ensure_ascii=False).lower()

    assert "clockwise" not in text and "counter" not in text
    square = build(jev_state, pieces=dict(PIECES, current_piece_mask=[[1, 1], [1, 1]]))
    assert square["piece_orientations"] == [[[1, 1], [1, 1]]], "a square has one orientation"
    l_shape = build(jev_state, pieces=dict(PIECES, current_piece_mask=[[1, 1], [1, 0]]))
    assert l_shape["piece_orientations"][0] == [[1, 1], [1, 0]], "index 0 is what the screen shows"
    assert len(l_shape["piece_orientations"]) == 4


def test_every_rotation_index_carries_the_shape_it_means(jev_state):
    mask = [[1, 0], [1, 0], [1, 1]]
    message = build(jev_state, pieces=dict(PIECES, current_piece_mask=mask))

    orientations = message["piece_orientations"]

    assert orientations[0] == mask, "index 0 is what the screen shows now"
    assert len(orientations) == 4
    assert {move["rotation"] for move in message["candidate_moves"]} <= set(
        range(len(orientations))
    )


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


def test_a_candidate_can_carry_the_board_it_would_produce(jev_state, simulator):
    """C/D arms: the environment may do the physics, the model only judges the result.

    The point of the arm is that the *only* new thing is the resulting board: no score, no
    holes, no height, no bumpiness, no best_candidate. Handing over a computed evaluation
    would measure "can Jev read a number", not "can Jev compare two states".
    """
    board = ["0" * 10] * 19 + ["1110000000"]
    mask = [[1, 1], [1, 1]]
    candidates = simulator.legal_placements(
        [[c if c == "1" else "." for c in row] for row in board], mask, gravity="down"
    )[:3]

    plain = jev_state.build_state(board, {"current_piece_mask": mask}, {}, None, candidates)
    with_results = jev_state.build_state(
        board, {"current_piece_mask": mask}, {}, None, candidates, resulting_boards=True
    )

    assert all("resulting_board" not in move for move in plain["candidate_moves"])
    results = [move.get("resulting_board") for move in with_results["candidate_moves"]]
    assert all(isinstance(result, list) and result for result in results)
    assert all(len(result) == 20 and len(result[0]) == 10 for result in results)
    # The piece is in the board it would produce, and the original board is untouched.
    assert sum(row.count("1") for result in results for row in result) > sum(
        row.count("1") for row in board
    )
    assert with_results["board_matrix"] == board
    # Nothing that smells like a score may ride along.
    for move in with_results["candidate_moves"]:
        assert set(move) == {"id", "rotation", "column", "resulting_board"}, move
    assert "resulting_board" in with_results["encoding"]


# --------------------------------------------------------------------------- #
# T4: the action-space message (message v2)
# --------------------------------------------------------------------------- #


def test_the_action_message_has_exactly_the_agreed_keys(jev_state, simulator):
    """The key set is the interface: whatever is missing is invisible, whatever is extra is
    something nobody agreed on. Pinned exactly, as the placement message is."""
    board = ["0" * 10] * 18 + ["0111111111", "0111111111"]
    message = jev_state.build_action_state(
        board,
        {
            "current_piece_mask": [[1, 1, 1, 1]],
            "current_piece_positions": [[0, 3], [0, 4], [0, 5], [0, 6]],
            "motion_vector": [1, 0],
        },
        {},
        None,
        rotation=0,
        column=3,
    )

    assert set(message) == {
        "task",
        "goal",
        "rules",
        "encoding",
        "board_matrix",
        "piece",
        "stack",
        "plan",
        "candidate_actions",
        "instructions",
    }
    assert set(message["piece"]) == {
        "mask",
        "positions",
        "orientations",
        "in_columns",
        "lands_on_row_if_untouched",
        "rows_of_fall_left",
        "actions_left",
        "done_so_far",
    }
    assert set(message["instructions"]) == {
        "task",
        "priorities_in_order",
        "where_to_aim",
        "tie_breakers",
        "important",
    }
    assert set(message["stack"]) >= {
        "column_heights",
        "tallest",
        "holes",
        "roughness",
        "dips_you_can_fill",
        "rows_near_completing",
    }


def test_every_candidate_action_says_what_it_does_and_what_it_leads_to(jev_state):
    board = ["0" * 10] * 19 + ["1111110000"]
    message = jev_state.build_action_state(
        board,
        {
            "current_piece_mask": [[1, 1, 1, 1]],
            "current_piece_positions": [[0, 0], [0, 1], [0, 2], [0, 3]],
            "motion_vector": [1, 0],
        },
        {},
        None,
        rotation=0,
        column=0,
    )

    actions = {action["id"]: action for action in message["candidate_actions"]}
    assert set(actions) <= {"left", "right", "rotate", "drop", "hold"}
    assert "right" in actions
    assert actions["right"]["does"], "every option explains itself in words"
    assert set(actions["right"]["then_lands"]) == {
        "in_columns",
        "on_row",
        "clears_lines",
        "new_holes",
        "roughness_after",
    }, "the consequence, computed here — the model does not do the physics"


def test_the_benchmark_variant_carries_no_computed_consequences(jev_state):
    """A benchmark that hands over our evaluation measures reading, not deciding."""
    board = ["0" * 10] * 19 + ["1111110000"]
    pieces = {
        "current_piece_mask": [[1, 1, 1, 1]],
        "current_piece_positions": [[0, 0], [0, 1], [0, 2], [0, 3]],
        "motion_vector": [1, 0],
    }

    leaky = jev_state.build_action_state(board, pieces, {}, None, consequences=True)
    clean = jev_state.build_action_state(board, pieces, {}, None, consequences=False, plan=False)

    assert "then_lands" not in clean["candidate_actions"][0]
    assert "then_lands" in leaky["candidate_actions"][0]
    assert clean["plan"] is None, "no plan either: a plan is a hint, and a hint is not a decision"
    assert leaky["plan"], "the play line gets the plan"


def test_the_message_says_how_much_time_is_left(jev_state):
    """The budget is the measured kind: rows to fall and the actions that fit in them."""
    board = ["0" * 10] * 19 + ["1111110000"]
    pieces = {
        "current_piece_mask": [[1, 1, 1, 1]],
        "current_piece_positions": [[3, 0], [3, 1], [3, 2], [3, 3]],
        "motion_vector": [1, 0],
    }

    without = jev_state.build_action_state(board, pieces, {}, None)
    with_budget = jev_state.build_action_state(
        board, pieces, {}, None, seconds_per_action=2.0, seconds_per_row=1.5
    )

    # How far the piece still has to fall is a fact about the board, so it is always there;
    # how many actions fit in that fall needs the timing, so it appears only with the budget.
    assert without["piece"]["rows_of_fall_left"] > 0
    assert without["piece"]["actions_left"] is None
    assert with_budget["piece"]["rows_of_fall_left"] == without["piece"]["rows_of_fall_left"]
    assert with_budget["piece"]["actions_left"] >= 1
    assert "actions_left" in with_budget["rules"]
    assert "actions_left" not in without["rules"]


def test_a_warning_marks_the_action_that_undoes_the_last_one(jev_state):
    """The reference's anti-ping-pong guard, at the option level (``index.html:163-165``)."""
    board = ["0" * 10] * 19 + ["1111110000"]
    pieces = {
        "current_piece_mask": [[1, 1, 1, 1]],
        "current_piece_positions": [[0, 4], [0, 5], [0, 6], [0, 7]],
        "motion_vector": [1, 0],
    }

    message = jev_state.build_action_state(board, pieces, {}, None, last_action="left")
    actions = {action["id"]: action for action in message["candidate_actions"]}

    assert actions["right"].get("warning"), "right undoes a left"
    assert not actions["left"].get("warning"), "and left does not undo itself"
