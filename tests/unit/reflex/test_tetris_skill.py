# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The tetris skill, offline: rules, policies and the arena that compares them.

No device, no model, no network: a skill's perception/policy/control are pure
functions, and the arena plays whole games in the reference simulator. This is the
surface on which a policy change — including a model-backed one — has to prove
itself before it gets a real game.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from artemis.reflex import registry
from artemis.services import systemone

SKILLS = Path(__file__).resolve().parents[3] / "skills"


@pytest.fixture(scope="module")
def tetris():
    return registry.load_skill("tetris", directory=SKILLS)


@pytest.fixture(scope="module")
def sim(tetris):
    import sys

    return sys.modules["artemis_skills_tetris.simulator"]


@pytest.fixture(scope="module")
def policy(tetris):
    import sys

    return sys.modules["artemis_skills_tetris.policy"]


@pytest.fixture(scope="module")
def arena(tetris):
    import sys

    return sys.modules["artemis_skills_tetris.arena"]


@pytest.fixture(scope="module")
def control(tetris):
    import sys

    return sys.modules["artemis_skills_tetris.control"]


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def test_shapes_rotate_into_the_expected_number_of_orientations(sim):
    assert len(sim.orientations("O")) == 1
    assert len(sim.orientations("I")) == 2
    assert len(sim.orientations("T")) == 4
    assert len(sim.orientations("S")) == 2
    assert sim.normalise(sim.SHAPES["T"]) == ((0, 0), (1, 0), (1, 1), (2, 0))


def test_a_piece_falls_to_the_floor_and_stacks(sim):
    board = sim.empty_board()
    dropped = sim.drop(board, "O", sim.orientations("O")[0], 4)

    assert dropped is not None
    assert dropped[-1][4] == "O" and dropped[-1][5] == "O"
    assert dropped[-2][4] == "O"
    assert board[-1][4] == sim.EMPTY, "the input board must not be mutated"


def test_full_rows_clear_and_everything_above_drops(sim):
    board = sim.empty_board()
    for x in range(sim.BOARD_WIDTH):
        board[-1][x] = "I"
    board[-2][0] = "T"

    cleared, count = sim.clear_lines(board)

    assert count == 1
    assert all(cell == sim.EMPTY for cell in cleared[-2]), "the cleared row's content is gone"
    assert cleared[-1][0] == "T", "the block above drops down into the cleared row"


def test_the_features_a_policy_scores_are_what_they_say(sim):
    board = sim.empty_board()
    board[-1] = ["I"] * 8 + [sim.EMPTY, sim.EMPTY]
    board[-2][0] = "T"  # a hole under nothing? no: a block above column 0

    assert sim.column_heights(board)[0] == 2
    assert sim.column_heights(board)[8] == 0
    assert sim.count_holes(board) == 0
    assert sim.bumpiness(board) >= 0
    assert sim.aggregate_height(board) == sum(sim.column_heights(board))
    assert sim.row_transitions(board) >= 0 and sim.column_transitions(board) >= 0
    assert sim.well_sums(board) >= 0


def test_legal_placements_never_include_an_impossible_column(sim):
    board = sim.empty_board()
    board[-1][0] = "I"

    placements = sim.legal_placements(board, "O")

    assert placements, "a fresh board must offer placements"
    for placement, cells in placements:
        assert sim.can_place(board, "O", cells, placement.column)
        assert placement.column + sim.shape_width(cells) <= sim.BOARD_WIDTH


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


def test_the_search_policy_is_deterministic(sim, policy):
    board = sim.empty_board()
    board[-1] = ["I"] * 4 + [sim.EMPTY] * 6

    first, detail = policy.choose(board, "T")
    second, _ = policy.choose(board, "T")

    assert first == second
    assert first is not None
    assert detail["score"] is not None


def test_the_policy_avoids_creating_a_hole_when_it_has_a_choice(sim, policy):
    """The classic sanity check for a Tetris heuristic."""
    board = sim.empty_board()
    board[-1][0] = "I"
    board[-1][1] = "I"
    board[-2][0] = "I"  # a well at column 0 with an overhang

    placement, _ = policy.choose(board, "I")

    assert placement is not None
    assert placement.column != 0 or placement.rotation == 1, "column 0 would bury the well"


def test_the_policy_reports_a_placement_and_counters_back_to_the_loop(policy):
    board = [["."] * 10 for _ in range(20)]
    board[-1] = ["I"] * 8 + [".", "."]
    state = {"board": board, "current": "T", "next": "S", "counters": {"lines": 3}}

    action = policy.decide(state, {"weights": policy.DEFAULT_WEIGHTS})

    assert action.state["placement"]["column"] >= 0
    assert action.state["counters"]["decisions"] == 1
    assert action.done is False


def test_the_policy_ends_the_session_at_the_calibrated_target(policy):
    board = [["."] * 10 for _ in range(20)]
    action = policy.decide(
        {"board": board, "current": "T", "lines": 12, "counters": {"lines": 12}},
        {"target_lines": 10},
    )

    assert action.done is True
    assert action.state["counters"]["target_reached"] == 10


def test_a_move_is_never_offered_by_the_model_but_generated_by_the_simulator(tetris, sim):
    """Legality by construction: the policy picks *among* simulator placements."""
    board = sim.empty_board()
    for placement, cells in sim.legal_placements(board, "S"):
        assert sim.drop(board, "S", cells, placement.column) is not None


# --------------------------------------------------------------------------- #
# Arena: the measurement instrument
# --------------------------------------------------------------------------- #


def test_the_arena_is_reproducible_for_a_seed(arena):
    first = arena.play_game(arena.search_policy(), seed=42, max_pieces=120)
    second = arena.play_game(arena.search_policy(), seed=42, max_pieces=120)
    other = arena.play_game(arena.search_policy(), seed=43, max_pieces=120)

    assert first.lines == second.lines and first.pieces == second.pieces
    assert (first.lines, first.pieces) != (other.lines, other.pieces)


def test_the_search_policy_clears_a_real_number_of_lines(arena):
    """A regression guard on the baseline: a weak policy is a broken simulator."""
    report = arena.run_tournament(games=3, seed=1, max_pieces=200)
    entry = report["policies"]["search"]

    assert entry["mean_lines"] >= 40, f"the search policy regressed: {entry}"
    assert entry["topped_out"] <= 1
    assert entry["mean_decision_ms"] < 200.0


def test_the_arena_reports_one_row_per_policy(arena):
    report = arena.run_tournament(
        games=2,
        seed=5,
        max_pieces=60,
        policies={"weak": lambda board, piece, next_piece: None},
    )

    assert set(report["policies"]) == {"weak"}
    assert report["policies"]["weak"]["games"] == 2
    assert report["policies"]["weak"]["mean_lines"] == 0


# --------------------------------------------------------------------------- #
# Control: a placement becomes gestures from the calibration
# --------------------------------------------------------------------------- #


def test_gestures_come_from_the_calibration_not_from_the_code(tetris, control):
    from artemis.reflex.contract import Action

    calibration = {
        "grid": {"origin_x": 100, "origin_y": 200, "cell_w": 50, "cell_h": 50},
        "gestures": {
            "rotate": {"kind": "tap", "at": [300, 1400]},
            "move": {"kind": "swipe", "y": 900, "duration_ms": 80},
            "hard_drop": {
                "kind": "swipe",
                "from": [300, 400],
                "to": [300, 1300],
                "duration_ms": 120,
            },
        },
    }
    action = Action(state={"placement": {"rotation": 2, "column": 7}, "current_column": 3})

    gestures = control.to_gestures(action, calibration)

    assert [g.kind for g in gestures] == ["tap", "tap", "swipe", "swipe"]
    assert gestures[0].points == ((300.0, 1400.0),)
    assert gestures[2].points[0][0] == 100 + 3 * 50 + 25  # start: current column centre
    assert gestures[2].points[1][0] > gestures[2].points[0][0]  # moving right
    assert gestures[3].description == "hard drop"


def test_without_a_calibrated_dialect_no_gesture_is_invented(tetris, control):
    from artemis.reflex.contract import Action

    assert control.to_gestures(Action(state={"placement": {"rotation": 1, "column": 2}}), {}) == []


# --------------------------------------------------------------------------- #
# The model backend, without a network
# --------------------------------------------------------------------------- #


def test_the_dispatcher_defaults_to_the_deterministic_search(tetris, monkeypatch):
    import sys

    dispatcher = sys.modules["artemis_skills_tetris.policy_dispatch"]
    board = [["."] * 10 for _ in range(20)]

    action = dispatcher.decide(
        {"board": board, "current": "T", "counters": {}}, {"policy_backend": "search"}
    )

    assert action.state["placement"] is not None
    assert action.state["counters"].get("source") is None  # search does not relabel itself


def test_the_model_backend_falls_back_to_search_when_it_cannot_be_reached(tetris, monkeypatch):
    import sys

    import importlib

    systemone_policy = importlib.import_module("artemis_skills_tetris.policy_systemone")
    dispatcher = sys.modules["artemis_skills_tetris.policy_dispatch"]
    # "Not configured" has to be simulated at the source: a real workspace keeps the
    # key in .env, so deleting the variable alone would still find one and make a
    # live API call from a unit test.
    monkeypatch.setattr(systemone, "api_key", lambda: None)
    monkeypatch.delenv(systemone.ENV_API_KEY, raising=False)
    systemone_policy.reset_telemetry()
    board = [["."] * 10 for _ in range(20)]

    action = dispatcher.decide(
        {"board": board, "current": "T", "counters": {}}, {"policy_backend": "systemone"}
    )

    assert action.state["placement"] is not None, "the loop must keep playing"
    assert action.state["counters"]["source"] == "search"
    assert "TYPESAFE_API_KEY" in action.state["counters"]["systemone_fallback"]
    assert systemone_policy.telemetry()["fallbacks"] == 1


def test_the_model_backend_uses_the_model_when_it_answers(tetris, monkeypatch):
    import sys

    import importlib

    systemone_policy = importlib.import_module("artemis_skills_tetris.policy_systemone")
    dispatcher = sys.modules["artemis_skills_tetris.policy_dispatch"]
    systemone_policy.reset_telemetry()
    board = [["."] * 10 for _ in range(20)]

    def fake_ask(state, questions, **kwargs):
        assert "board_rows_top_to_bottom" in state, "the model sees the board as text"
        assert len(questions) == len(state["candidate_moves"])
        # Pick the last candidate on purpose: a different choice than the search's.
        return {f"cand_{len(questions) - 1}": {"type": "noul", "noul": 0.97}}

    monkeypatch.setattr(systemone, "ask", fake_ask)

    action = dispatcher.decide(
        {"board": board, "current": "T", "counters": {}}, {"policy_backend": "systemone"}
    )

    assert action.state["counters"]["source"] == "systemone"
    assert action.state["counters"]["model_best_probability"] == 0.97
    assert systemone_policy.telemetry()["calls"] == 1


def test_a_confident_model_answer_is_not_second_guessed(tetris, monkeypatch):
    """The model's own ranking decides; the search only breaks ties and abstentions."""
    import sys

    import importlib

    systemone_policy = importlib.import_module("artemis_skills_tetris.policy_systemone")
    sim = sys.modules["artemis_skills_tetris.simulator"]
    board = sim.empty_board()
    candidates = sim.legal_placements(board, "T")
    target_index = len(candidates) - 1

    def fake_ask(state, questions, **kwargs):
        return {f"cand_{target_index}": {"type": "noul", "noul": 0.99}}

    monkeypatch.setattr(systemone, "ask", fake_ask)
    action = systemone_policy.decide({"board": board, "current": "T", "counters": {}}, {})

    chosen = action.state["placement"]
    expected = candidates[target_index][0]
    assert (chosen["rotation"], chosen["column"]) == (expected.rotation, expected.column)


def test_the_manifest_switches_backend_by_calibration_file(tetris):
    calibration = json.loads((SKILLS / "tetris" / "calibration.json").read_text(encoding="utf-8"))

    assert calibration["policy_backend"] in {"search", "systemone"}
    assert calibration["policy_backend"] == "search", (
        "the shipped default must not depend on a network service"
    )


# --------------------------------------------------------------------------- #
# Gravity as a parameter, shapes as geometry (T7)
# --------------------------------------------------------------------------- #

#: A 2x3 rectangle: not one of the standard seven, and the point of the exercise.
RECT = [[1, 1], [1, 1], [1, 1]]
#: An L, as geometry rather than a letter.
L_MASK = [[1, 1, 1], [1, 0, 0]]


def filled(board, sim):
    return {
        (y, x) for y, row in enumerate(board) for x, cell in enumerate(row) if cell != sim.EMPTY
    }


def test_gravity_up_lands_the_piece_at_the_ceiling(sim):
    """The game can switch the drop direction mid-session (Review Focus 3)."""
    landed = sim.drop(sim.empty_board(), "O", 4, gravity="up")

    assert filled(landed, sim) == {(0, 4), (0, 5), (1, 4), (1, 5)}


def test_gravity_left_lands_the_piece_at_the_left_wall(sim):
    landed = sim.drop(sim.empty_board(), "O", 5, gravity="left")

    assert filled(landed, sim) == {(5, 0), (5, 1), (6, 0), (6, 1)}


def test_gravity_right_lands_the_piece_at_the_right_wall(sim):
    landed = sim.drop(sim.empty_board(), "O", 5, gravity="right")

    assert filled(landed, sim) == {(5, 8), (5, 9), (6, 8), (6, 9)}


def test_a_mask_stops_against_the_stack_not_only_the_wall(sim):
    board = sim.empty_board()
    board[5][2] = "I"

    landed = sim.drop(board, RECT, 3, gravity="left")

    # (5, 2) is the block that stopped it; the piece's own six cells are the rest.
    assert filled(landed, sim) - {(5, 2)} == {
        (3, 3),
        (3, 4),
        (4, 3),
        (4, 4),
        (5, 3),
        (5, 4),
    }
    assert landed[5][2] == "I", "the block it stopped against is still there"


@pytest.mark.parametrize("gravity", ["down", "up", "left", "right"])
def test_a_custom_mask_lands_in_every_gravity_without_leaving_the_board(sim, gravity):
    """Review Focus 4: the shape set is not the standard seven, so give it geometry."""
    board = sim.empty_board()

    landed = sim.drop(board, RECT, 4, gravity=gravity)

    assert landed is not None, gravity
    cells = filled(landed, sim)
    assert len(cells) == 6
    assert all(0 <= y < sim.BOARD_HEIGHT and 0 <= x < sim.BOARD_WIDTH for y, x in cells)
    assert board == sim.empty_board(), "the input board must not be mutated"


@pytest.mark.parametrize("gravity", ["down", "up", "left", "right"])
def test_legal_placements_offer_the_custom_mask_in_every_gravity(sim, gravity):
    board = sim.empty_board()
    board[-1][4] = "I"

    placements = sim.legal_placements(board, RECT, gravity=gravity)

    assert placements, gravity
    vertical = gravity in {"down", "up"}
    extent = sim.BOARD_WIDTH if vertical else sim.BOARD_HEIGHT
    for placement, cells in placements:
        span = sim.shape_width(cells) if vertical else sim.shape_height(cells)
        assert 0 <= placement.column <= extent - span, (placement, cells)
        assert sim.drop(board, cells, placement.column, gravity=gravity) is not None


def test_the_standard_scenarios_are_unchanged(sim):
    """The regression the plan asks for: the old call still means what it meant."""
    board = sim.empty_board()
    board[-1][0] = "I"
    settled = filled(board, sim)

    legacy = sim.drop(board, "O", sim.orientations("O")[0], 4)
    by_mask = sim.drop(board, [[1, 1], [1, 1]], 4)

    # The same cells, and only the marker differs: a letter names a shape, and a
    # game with custom shapes has none - so geometry lands as MASK_FILL instead.
    placed = {(18, 4), (18, 5), (19, 4), (19, 5)}
    assert filled(legacy, sim) == filled(by_mask, sim) == settled | placed
    assert legacy[19][4] == "O", "the letter path still labels its cells (the arena needs it)"
    assert by_mask[19][4] == sim.MASK_FILL
    assert sim.drop(board, "O", sim.orientations("O")[0], 4, gravity="down") == legacy


def test_orientations_from_mask_starts_where_the_piece_is(sim):
    """Index 0 is what the screen shows: the gesture layer taps `rotation` times."""
    assert sim.orientations_from_mask(L_MASK)[0] == L_MASK
    assert len(sim.orientations_from_mask(L_MASK)) == 4
    assert len(sim.orientations_from_mask([[1, 1], [1, 1]])) == 1
    assert len(sim.orientations_from_mask(RECT)) == 2


def test_a_letter_and_its_geometry_are_the_same_piece(sim):
    """The seven names stay as a convenience; the mask is the real interface."""
    for letter, shape in sim.SHAPES.items():
        from_cells = sim.mask_cells(shape)

        assert from_cells == sim.normalise(shape), letter
        assert sim.mask_bitmap(shape) == sim.mask_bitmap(from_cells)


def test_a_placement_that_cannot_fit_is_refused_never_wrapped(sim):
    """A coordinate off the board returns nothing; it never wraps to the other side.

    Pinned after a mutation: deleting the explicit coordinate guard changed no test,
    because the per-cell bounds check refuses the same placements. The observable
    contract is what matters and is what is asserted here.
    """
    assert sim.drop(sim.empty_board(), [[1] * 11], 0) is None
    assert sim.drop(sim.empty_board(), "O", 999) is None
    assert sim.drop(sim.empty_board(), "O", -3) is None
    assert sim.drop(sim.empty_board(), "O", 4, gravity="up") is not None


def test_an_unknown_gravity_is_refused_rather_than_guessed(sim):
    with pytest.raises(ValueError, match="gravity"):
        sim.drop(sim.empty_board(), "O", 4, gravity="sideways")


def test_the_calibrated_gravity_options_are_what_the_simulator_supports(tetris, sim):
    """The calibration file and the rules module must agree about the axis set."""
    calibration = json.loads((SKILLS / "tetris" / "calibration.json").read_text(encoding="utf-8"))

    mechanics = calibration["mechanics"]
    assert set(mechanics["gravity_options"]) == set(sim.GRAVITIES)
    assert isinstance(mechanics["shapes"], dict), "the app's custom shape set, empty until measured"


def test_the_search_policy_tells_the_control_layer_where_the_piece_is(policy):
    """A placement without the piece's current column cannot become a horizontal move."""
    board = [["."] * 10 for _ in range(20)]
    measured = {
        "board": board,
        "current": "T",
        "current_piece_positions": [[14, 3], [15, 3]],
        "counters": {},
    }
    legacy = {"board": board, "current": "T", "falling_cells": [(4, 14), (4, 15)], "counters": {}}

    assert policy.decide(measured, {}).state["current_column"] == 3
    assert policy.decide(legacy, {}).state["current_column"] == 4
