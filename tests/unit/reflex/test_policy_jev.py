"""T8: Jev ranks the moves the simulator allows — and nothing else can happen.

The legality boundary is the whole point of this file. Candidates come from
``legal_placements``, the model is only asked *which of these* is best, and every
answer is mapped back to the candidate it named. A model cannot invent a move, and
neither can a parsing bug: an id that is not in the candidate list is an abstention,
not an action.

The property test at the end is the one that matters most: 200 random boards, and
every returned ``(rotation, column)`` must be a legal placement in the gravity the
piece was measured to be falling in (Review Focus 3). It runs both when the model
answers and when it does not, because the fallback is a second way to produce a move.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from artemis.reflex.registry import load_skill
from artemis.services import systemone

SKILLS = Path(__file__).resolve().parents[3] / "skills"
FRESH_BOARD = ["0" * 10] * 20
SQUARE = [[1, 1], [1, 1]]


@pytest.fixture(scope="module")
def jev_policy():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.policy_jev")


@pytest.fixture(scope="module")
def simulator():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.simulator")


@pytest.fixture(scope="module")
def jev_state():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.jev_state")


@pytest.fixture(scope="module")
def calibration():
    return {
        "policy_backend": "jev",
        "systemone_timeout_s": 1.0,
        "jev": {"mode": "noul", "min_confidence": 0.05, "options_cap": 40},
        "mechanics": {"gravity_options": ["down", "up", "left", "right"]},
    }


def state_for(board_matrix=None, *, mask=None, motion=None, counters=None, **extra):
    state = {
        "board_matrix": list(board_matrix or FRESH_BOARD),
        "current_piece_mask": mask if mask is not None else SQUARE,
        "current_piece_positions": [[0, 3], [0, 4], [1, 3], [1, 4]],
        "motion_vector": motion,
        "next_preview": None,
        "counters": dict(counters or {}),
    }
    state.update(extra)
    return state


def to_board(simulator, board_matrix):
    return [
        [simulator.EMPTY if cell in {"0", "."} else "#" for cell in row] for row in board_matrix
    ]


def candidates_for(simulator, state, gravity="down"):
    return simulator.legal_placements(
        to_board(simulator, state["board_matrix"]), state["current_piece_mask"], gravity=gravity
    )


def answering(monkeypatch, probabilities):
    """A fake ``systemone.ask`` answering ``cand_i`` questions with given probabilities."""

    def fake_ask(payload, questions, **kwargs):
        assert all(question["type"] == "noul" for question in questions.values())
        return {
            f"cand_{index}": {"type": "noul", "noul": probabilities[index]}
            for index in range(len(probabilities))
        }

    monkeypatch.setattr(systemone, "ask", fake_ask)


# --------------------------------------------------------------------------- #
# The model's answer decides, among legal moves
# --------------------------------------------------------------------------- #


def test_the_candidate_the_model_is_confident_about_is_the_one_played(
    jev_policy, simulator, calibration, monkeypatch
):
    state = state_for()
    candidates = candidates_for(simulator, state)
    target = len(candidates) - 1
    probabilities = [0.01] * len(candidates)
    probabilities[target] = 0.9
    answering(monkeypatch, probabilities)
    jev_policy.reset_telemetry()

    action = jev_policy.decide(state, calibration)

    expected = candidates[target][0]
    assert action.state["placement"] == {"rotation": expected.rotation, "column": expected.column}
    assert action.state["counters"]["source"] == "jev"
    assert action.done is False


def test_the_message_the_model_is_asked_about_carries_geometry_not_letters(
    jev_policy, simulator, calibration, monkeypatch
):
    seen = {}

    def fake_ask(payload, questions, **kwargs):
        seen.update(payload)
        return {f"cand_{index}": {"type": "noul", "noul": 0.9} for index in range(len(questions))}

    monkeypatch.setattr(systemone, "ask", fake_ask)

    jev_policy.decide(state_for(), calibration)

    assert seen["current_piece_mask"] == SQUARE
    assert set(seen) == {
        "board_matrix",
        "current_piece_mask",
        "current_piece_positions",
        "motion_vector",
        "mechanics",
        "next_preview",
        "candidate_moves",
    }
    assert json.dumps(seen), "the message has to survive JSON, it is sent as JSON"


def test_a_hedging_model_hands_the_decision_to_the_search(
    jev_policy, simulator, calibration, monkeypatch
):
    state = state_for()
    answering(monkeypatch, [0.01, 0.02, 0.0])
    jev_policy.reset_telemetry()

    action = jev_policy.decide(state, calibration)

    assert action.state["counters"]["source"] == "search"
    legal = {
        (placement.rotation, placement.column) for placement, _ in candidates_for(simulator, state)
    }
    played = action.state["placement"]
    assert (played["rotation"], played["column"]) in legal


def test_an_unavailable_model_falls_back_and_says_so(jev_policy, calibration, monkeypatch):
    def unavailable(*args, **kwargs):
        raise systemone.SystemOneUnavailable("HTTP 429")

    monkeypatch.setattr(systemone, "ask", unavailable)
    jev_policy.reset_telemetry()

    action = jev_policy.decide(state_for(), calibration)
    telemetry = jev_policy.telemetry()

    assert telemetry["fallbacks"] == 1
    assert "429" in str(telemetry["last_error"])
    assert action.state["counters"]["source"] == "search"


def test_an_answer_naming_nothing_is_an_abstention_not_an_action(
    jev_policy, simulator, calibration, monkeypatch
):
    """An id nobody offered cannot become a move: the answer is discarded, not read."""
    state = state_for()
    monkeypatch.setattr(
        systemone,
        "ask",
        lambda payload, questions, **kwargs: {"ghost_move": {"type": "noul", "noul": 0.99}},
    )
    jev_policy.reset_telemetry()

    action = jev_policy.decide(state, calibration)

    assert action.state["counters"]["source"] == "search", "an unusable answer is not a move"
    legal = {(p.rotation, p.column) for p, _ in candidates_for(simulator, state)}
    played = action.state["placement"]
    assert (played["rotation"], played["column"]) in legal


# --------------------------------------------------------------------------- #
# The choice variant
# --------------------------------------------------------------------------- #


def test_choice_mode_asks_one_criterion_per_candidate(
    jev_policy, simulator, calibration, monkeypatch
):
    state = state_for()
    candidates = candidates_for(simulator, state)
    seen = {}
    offered = candidates[2][0]

    def fake_ask(payload, questions, **kwargs):
        seen.update(questions)
        seen["payload"] = payload
        return {
            "placement": {
                "type": "choice",
                "choice": f"r{offered.rotation}c{offered.column}",
                "probabilities": {f"r{offered.rotation}c{offered.column}": 0.7},
            }
        }

    monkeypatch.setattr(systemone, "ask", fake_ask)
    mode = dict(calibration, jev={"mode": "choice", "min_confidence": 0.05, "options_cap": 40})
    jev_policy.reset_telemetry()

    action = jev_policy.decide(state, mode)

    criteria = seen["placement"]["criteria"]
    assert len(criteria) == len(candidates)
    assert set(criteria) == {move["id"] for move in seen["payload"]["candidate_moves"]}
    assert action.state["placement"] == {"rotation": offered.rotation, "column": offered.column}
    assert action.state["counters"]["mode"] == "choice"


def test_choice_mode_still_plays_a_legal_move_when_the_winner_is_unknown(
    jev_policy, simulator, calibration, monkeypatch
):
    state = state_for()
    monkeypatch.setattr(
        systemone,
        "ask",
        lambda payload, questions, **kwargs: {"placement": {"type": "choice", "choice": "r9c9"}},
    )
    mode = dict(calibration, jev={"mode": "choice", "min_confidence": 0.05, "options_cap": 40})
    jev_policy.reset_telemetry()

    action = jev_policy.decide(state, mode)

    legal = {(p.rotation, p.column) for p, _ in candidates_for(simulator, state)}
    played = action.state["placement"]
    assert (played["rotation"], played["column"]) in legal


# --------------------------------------------------------------------------- #
# Gravity is followed, not assumed
# --------------------------------------------------------------------------- #


def test_the_candidates_follow_the_measured_gravity(
    jev_policy, simulator, calibration, monkeypatch
):
    """Review Focus 3: switch gravity, and the moves on offer switch with it.

    The stack sits on the floor and covers the left half, so under "up" the piece
    cannot even enter those columns while under "down" every column is available.
    """
    board_matrix = ["0" * 10 for _ in range(18)] + ["1111000000", "1111000000"]
    state = state_for(board_matrix, motion=[-1, 0])  # the piece is moving up
    seen = {}

    def fake_ask(payload, questions, **kwargs):
        seen.update(payload)
        return {f"cand_{index}": {"type": "noul", "noul": 0.8} for index in range(len(questions))}

    monkeypatch.setattr(systemone, "ask", fake_ask)

    action = jev_policy.decide(state, calibration)

    assert seen["mechanics"]["gravity_direction"] == "up"
    rising = {(p.rotation, p.column) for p, _ in candidates_for(simulator, state, gravity="up")}
    falling = {(p.rotation, p.column) for p, _ in candidates_for(simulator, state, gravity="down")}
    played = action.state["placement"]
    assert (played["rotation"], played["column"]) in rising
    assert rising != falling, (
        "the two gravities must offer different moves for this to mean anything"
    )

    board = to_board(simulator, state["board_matrix"])
    upwards = simulator.drop(board, state["current_piece_mask"], played["column"], gravity="up")
    downwards = simulator.drop(board, state["current_piece_mask"], played["column"], gravity="down")
    assert upwards != downwards, "the same (rotation, column) lands differently per gravity"


def test_without_a_piece_there_is_no_decision_to_make(jev_policy, calibration, monkeypatch):
    monkeypatch.setattr(systemone, "ask", lambda *args, **kwargs: pytest.fail("no call expected"))

    action = jev_policy.decide(state_for(mask=[]), calibration)

    assert action.gestures == ()
    assert action.done is False
    assert action.state["counters"]["no_piece"] == 1


# --------------------------------------------------------------------------- #
# The property: a move nobody allowed is impossible
# --------------------------------------------------------------------------- #


def test_two_hundred_random_boards_only_ever_produce_legal_moves(
    jev_policy, jev_state, simulator, calibration, monkeypatch
):
    """The plan's property test: legality cannot depend on what the model answers."""
    import random

    source = random.Random(20261012)
    shapes = [simulator.mask_bitmap(shape) for shape in simulator.SHAPES.values()]
    gravity_options = ["down", "up", "left", "right"]

    for _ in range(200):
        board_matrix = ["0" * 10 for _ in range(20)]
        for row in range(6, 20):
            for column in range(10):
                if source.random() < 0.25:
                    board_matrix[row] = (
                        board_matrix[row][:column] + "1" + board_matrix[row][column + 1 :]
                    )
        mask = source.choice(shapes)
        motion = source.choice([[1, 0], [-1, 0], [0, -1], [0, 1], None])
        gravity = jev_state.gravity_direction(motion, "down")
        state = state_for(board_matrix, mask=mask, motion=motion)
        legal = {
            (placement.rotation, placement.column)
            for placement, _ in candidates_for(simulator, state, gravity=gravity)
        }

        def maybe_answer(payload, questions, **kwargs):
            if source.random() < 0.5:
                raise systemone.SystemOneUnavailable("flaky network")
            best = source.randrange(len(questions))
            return {
                f"cand_{index}": {"type": "noul", "noul": 0.9 if index == best else 0.01}
                for index in range(len(questions))
            }

        monkeypatch.setattr(systemone, "ask", maybe_answer)

        action = jev_policy.decide(state, calibration)

        if not legal:
            assert action.state["placement"] is None, "no legal move means no move"
            assert action.done is True, "a board with no legal move ends the session"
            continue
        played = action.state["placement"]
        assert (played["rotation"], played["column"]) in legal, (
            f"{played} is not one of {len(legal)} legal moves"
        )
        assert gravity in gravity_options


def test_the_dispatcher_routes_the_jev_backend(jev_policy, calibration, monkeypatch):
    dispatcher = importlib.import_module("artemis_skills_tetris.policy_dispatch")
    monkeypatch.setattr(
        systemone,
        "ask",
        lambda payload, questions, **kwargs: {
            f"cand_{index}": {"type": "noul", "noul": 0.9} for index in range(len(questions))
        },
    )

    action = dispatcher.decide(state_for(), dict(calibration, policy_backend="jev"))

    assert action.state["counters"]["source"] == "jev"
