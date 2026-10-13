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
    jev_policy.reset_decisions()

    action = jev_policy.decide(state, calibration)

    expected = candidates[target][0]
    assert action.state["placement"] == {"rotation": expected.rotation, "column": expected.column}
    assert action.state["counters"]["source"] == "jev"
    assert action.done is False


def test_the_message_the_model_is_asked_about_carries_geometry_not_letters(
    jev_policy, simulator, calibration, monkeypatch
):
    jev_policy.reset_decisions()
    seen = {}

    def fake_ask(payload, questions, **kwargs):
        seen.update(payload)
        return {f"cand_{index}": {"type": "noul", "noul": 0.9} for index in range(len(questions))}

    monkeypatch.setattr(systemone, "ask", fake_ask)

    jev_policy.decide(state_for(), calibration)

    assert seen["current_piece_mask"] == SQUARE
    assert {
        "board_matrix",
        "current_piece_mask",
        "current_piece_positions",
        "motion_vector",
        "mechanics",
        "next_preview",
        "candidate_moves",
        "task",
        "goal",
        "execution",
        "encoding",
        "piece_orientations",
    } <= set(seen)
    assert json.dumps(seen), "the message has to survive JSON, it is sent as JSON"


def test_a_hedging_model_hands_the_decision_to_the_search(
    jev_policy, simulator, calibration, monkeypatch
):
    state = state_for()
    answering(monkeypatch, [0.01, 0.02, 0.0])
    jev_policy.reset_decisions()

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
    jev_policy.reset_decisions()

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
    jev_policy.reset_decisions()

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
    jev_policy.reset_decisions()

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
    jev_policy.reset_decisions()

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


# --------------------------------------------------------------------------- #
# Counting, and asking once per piece (fixed after review)
# --------------------------------------------------------------------------- #


def test_a_hedging_model_is_counted_as_the_fallback_it_is(jev_policy, calibration, monkeypatch):
    """The number T10 reports: a low-confidence answer means the search played."""
    answering(monkeypatch, [0.01, 0.02, 0.0])
    jev_policy.reset_decisions()

    action = jev_policy.decide(state_for(), calibration)
    telemetry = jev_policy.telemetry()

    assert action.state["counters"]["source"] == "search"
    assert telemetry["fallbacks"] == 1, "the search played; that is a fallback"
    assert telemetry["calls"] == 1


def test_a_confident_answer_is_not_counted_as_a_fallback(jev_policy, calibration, monkeypatch):
    answering(monkeypatch, [0.9, 0.01, 0.01])
    jev_policy.reset_decisions()

    jev_policy.decide(state_for(), calibration)

    assert jev_policy.telemetry()["fallbacks"] == 0


def test_the_model_is_asked_once_per_piece_not_once_per_frame(
    jev_policy, simulator, calibration, monkeypatch
):
    """The plan's budget: one Jev decision per piece (the inner loop is 2-3 fps).

    The same piece one row lower is the same decision: where it lands does not depend
    on the row it is in when the question is asked.
    """
    asked = []

    def fake_ask(payload, questions, **kwargs):
        asked.append(payload)
        return {f"cand_{index}": {"type": "noul", "noul": 0.9} for index in range(len(questions))}

    monkeypatch.setattr(systemone, "ask", fake_ask)
    jev_policy.reset_decisions()
    higher = state_for(
        mask=[[1, 1], [1, 1]],
        motion=[1, 0],
        current_piece_positions=[[14, 3], [14, 4], [15, 3], [15, 4]],
    )
    lower = state_for(
        mask=[[1, 1], [1, 1]],
        motion=[1, 0],
        current_piece_positions=[[15, 3], [15, 4], [16, 3], [16, 4]],
    )

    first = jev_policy.decide(higher, calibration)
    second = jev_policy.decide(lower, calibration)

    assert len(asked) == 1, "the same piece is not worth a second call"
    assert second.state["counters"]["reused"] == 1
    assert jev_policy.telemetry()["reuses"] == 1
    waiting = second.state.get("counters", {}).get("waiting_for_lock")
    assert second.state.get("placement") == first.state.get("placement") or waiting, (
        "a frame with nothing left to do says so instead of repeating the move"
    )


def test_a_new_piece_is_a_new_question(jev_policy, simulator, calibration, monkeypatch):
    asked = []

    def fake_ask(payload, questions, **kwargs):
        asked.append(payload)
        return {f"cand_{index}": {"type": "noul", "noul": 0.9} for index in range(len(questions))}

    monkeypatch.setattr(systemone, "ask", fake_ask)
    jev_policy.reset_decisions()
    jev_policy.decide(state_for(), calibration)
    jev_policy.decide(
        state_for(mask=[[1, 1], [1, 0]], current_piece_positions=[[14, 6], [15, 6]]), calibration
    )

    assert len(asked) == 2, "a different piece is a different question"


# --------------------------------------------------------------------------- #
# The decision has to reach the gesture layer
# --------------------------------------------------------------------------- #


def test_the_decision_carries_the_pieces_column_so_the_move_actually_runs(
    jev_policy, simulator, calibration, monkeypatch
):
    """Without it the gesture layer only rotates and drops: no column is ever chosen."""
    control = importlib.import_module("artemis_skills_tetris.control")
    positions = [[14, 3], [14, 4], [15, 3], [15, 4]]
    state = state_for(mask=[[1, 1], [1, 1]], motion=[1, 0], current_piece_positions=positions)
    candidates = candidates_for(simulator, state)
    elsewhere = next(
        index for index, (placement, _cells) in enumerate(candidates) if placement.column != 3
    )
    probabilities = [0.01] * len(candidates)
    probabilities[elsewhere] = 0.9
    answering(monkeypatch, probabilities)
    jev_policy.reset_decisions()

    action = jev_policy.decide(state, calibration)
    real = json.loads((SKILLS / "tetris" / "calibration.json").read_text(encoding="utf-8"))
    gestures = control.to_gestures(action, real)

    assert action.state["current_column"] == 3, (
        "the piece's left edge, as the gesture layer reads it"
    )
    assert any(gesture.description.startswith("move") for gesture in gestures), gestures


def test_every_question_names_the_candidate_it_asks_about(jev_policy, calibration, monkeypatch):
    """Measured: the first real call sent 18 *identical* questions, none of which said
    which candidate "this" was - an unanswerable question, answered with a flat 0.35."""
    jev_policy.reset_decisions()
    seen = {}

    def fake_ask(payload, questions, **kwargs):
        seen["payload"] = payload
        seen["questions"] = questions
        return {key: {"type": "noul", "noul": 0.5} for key in questions}

    monkeypatch.setattr(systemone, "ask", fake_ask)

    jev_policy.decide(state_for(), calibration)

    moves = seen["payload"]["candidate_moves"]
    assert len(seen["questions"]) == len(moves)
    for index, move in enumerate(moves):
        instructions = seen["questions"][f"cand_{index}"]["instructions"]
        assert move["id"] in instructions, f"cand_{index} does not name {move['id']}"
        assert str(move["column"]) in instructions


# --------------------------------------------------------------------------- #
# Two phases: rotate first, then place - each measured on its own frame
# (following the device truth: rotation is clockwise and pivots on the piece's
#  second cell, so a move computed *before* the turn lands in the wrong column)
# --------------------------------------------------------------------------- #


def _three_bar(mask, positions):
    return state_for(mask=mask, motion=[1, 0], current_piece_positions=[list(c) for c in positions])


def test_a_turn_does_not_buy_a_second_decision(jev_policy, simulator, calibration, monkeypatch):
    """The budget is one Jev call per piece. Rotating it is the same piece."""
    asked = []

    before = _three_bar([[1, 1, 1]], [(2, 3), (2, 4), (2, 5)])
    after = _three_bar([[1], [1], [1]], [(2, 5), (3, 5), (4, 5)])  # 转过去了、也移位了
    # 让模型选一个 rotation=1 的候选（横放转竖放），这样第二帧看到竖条 = 旋转已完成
    candidates = candidates_for(simulator, before)
    target = next(
        index for index, (placement, _) in enumerate(candidates) if placement.rotation == 1
    )

    def fake_ask(payload, questions, **kwargs):
        asked.append(payload)
        return {
            f"cand_{index}": {"type": "noul", "noul": 0.9 if index == target else 0.05}
            for index in range(len(questions))
        }

    monkeypatch.setattr(systemone, "ask", fake_ask)
    jev_policy.reset_decisions()

    first = jev_policy.decide(before, calibration)
    second = jev_policy.decide(after, calibration)

    assert len(asked) == 1, "a rotated piece is the same piece"
    assert first.state["phase"] == "rotate"
    assert second.state["phase"] == "move", "the second frame places what the first one turned"
    assert second.state["placement"] == first.state["placement"]


def test_the_rotate_frame_only_asks_for_the_taps_still_missing(
    jev_policy, simulator, calibration, monkeypatch
):
    mask = [[1, 1, 1]]
    state = _three_bar(mask, [(2, 3), (2, 4), (2, 5)])
    candidates = candidates_for(simulator, state)
    target = next(
        index for index, (placement, _) in enumerate(candidates) if placement.rotation == 1
    )
    probabilities = [0.01] * len(candidates)
    probabilities[target] = 0.9
    answering(monkeypatch, probabilities)
    jev_policy.reset_decisions()

    turn = jev_policy.decide(state, calibration)
    already_turned = jev_policy.decide(
        _three_bar([[1], [1], [1]], [(2, 5), (3, 5), (4, 5)]), calibration
    )

    assert turn.state["rotate_taps"] == 1, "one clockwise turn takes the bar from flat to upright"
    assert not already_turned.state.get("rotate_taps"), "already upright: nothing left to turn"


def test_a_piece_whose_orientations_repeat_needs_no_taps_to_reach_them(jev_policy, simulator):
    """An O piece is one shape however it is turned; an S/Z has two, and rotation 2 is the first.

    The shape list is de-duplicated, which is what keeps the modulo honest: asking for
    "rotation 2" of a piece whose second shape *is* its first has to cost zero taps, or the
    rotate phase can never finish.
    """
    square = [[1, 1], [1, 1]]
    square_shapes = simulator.orientations_from_mask(square)
    assert len(square_shapes) == 1, "a square has one shape, however many times it is turned"
    assert jev_policy._taps_to_reach(square, square_shapes, 2) == 0
    assert jev_policy._taps_to_reach(square, square_shapes, 3) == 0

    zed = [[1, 1, 0], [0, 1, 1]]
    zed_shapes = simulator.orientations_from_mask(zed)
    assert len(zed_shapes) == 2, "an S/Z has two shapes, and turning it four times repeats them"
    assert jev_policy._taps_to_reach(zed, zed_shapes, 0) == 0
    assert jev_policy._taps_to_reach(zed, zed_shapes, 1) == 1
    assert jev_policy._taps_to_reach(zed, zed_shapes, 2) == 0, "rotation 2 is shape 0 again"
    assert jev_policy._taps_to_reach(zed_shapes[1], zed_shapes, 2) == 1
    # A shape that matches nothing on the list falls back to the requested count, which is
    # what the rotate guard then catches if it never resolves.
    assert jev_policy._taps_to_reach([[1, 0], [0, 1]], zed_shapes, 1) == 1


def test_the_record_says_how_close_the_decision_was(
    jev_policy, simulator, calibration, monkeypatch, tmp_path
):
    """Confidence alone cannot tell "wrong and sure" from "wrong and unsure".

    A single number is ambiguous in both modes: under `noul` it is one independent
    "is this the best" probability, under `choice` it is a share of a normalised
    distribution. The record therefore carries the runner-up, the margin and the
    distribution kind, plus the next-best candidates, which is what a reader needs to judge
    a decision rather than just read it.
    """
    import json as _json

    state = _three_bar([[1, 1, 1]], [(2, 3), (2, 4), (2, 5)])
    candidates = candidates_for(simulator, state)
    probabilities = [0.02] * len(candidates)
    probabilities[0] = 0.9
    probabilities[1] = 0.5
    answering(monkeypatch, probabilities)
    jev_policy.reset_decisions()

    dump = tmp_path / "jev.jsonl"
    monkeypatch.setenv("ARTEMIS_JEV_DUMP", str(dump))
    jev_policy.decide(state, calibration)

    record = _json.loads(dump.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["mode"] == "noul"
    assert record["distribution"] == "independent", "noul answers do not sum to one"
    assert record["confidence"] == pytest.approx(0.9, abs=0.01)
    assert record["runner_up"] == pytest.approx(0.5, abs=0.01), "the second-best answer"
    assert record["margin"] == pytest.approx(0.4, abs=0.02)
    assert record["alternatives"], "the next-best candidates, in order"
    assert all(isinstance(name, str) for name in record["alternatives"])


def test_every_jev_call_can_be_recorded_when_asked_for(
    jev_policy, simulator, calibration, monkeypatch, tmp_path
):
    """The input Jev received and the output it returned, on disk — off unless asked.

    Without this the payload exists only while it is in flight: the trace says `source=jev`
    and which placement won, but not what the model was asked or what it answered, which is
    what you need when a decision is wrong. `ARTEMIS_JEV_DUMP` is how the play script turns
    it on; a skill must not write files on its own.
    """
    import json as _json
    import os

    state = _three_bar([[1, 1, 1]], [(2, 3), (2, 4), (2, 5)])
    probabilities = [0.9] * len(candidates_for(simulator, state))
    answering(monkeypatch, probabilities)
    jev_policy.reset_decisions()

    dump = tmp_path / "jev.jsonl"
    monkeypatch.delenv("ARTEMIS_JEV_DUMP", raising=False)
    jev_policy.decide(state, calibration)
    assert not dump.exists(), "no environment variable, no file"

    monkeypatch.setenv("ARTEMIS_JEV_DUMP", str(dump))
    jev_policy.reset_decisions()
    jev_policy.decide(state, calibration)

    assert dump.exists(), "the call left no record"
    record = _json.loads(dump.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["input"], "the message that went out is the point of the record"
    assert record["question_count"], "and the questions it was asked"
    assert any("rotation" in str(q) for q in record["questions"].values()), (
        "each question names the candidate it asks about"
    )
    assert record["answers"], "and the answers that came back"
    # `seconds` may round to 0.0 here: the fake `systemone.ask` answers instantly, while a
    # real call took 7.63 s on the device (skills/tetris/calibration.json#systemone_timeout_note).
    assert record["chosen"] and record["seconds"] >= 0
    assert os.environ["ARTEMIS_JEV_DUMP"] == str(dump)


def test_one_piece_turns_once_then_shifts_once(jev_policy, simulator, calibration, monkeypatch):
    """The operator's rule: a piece is turned once and shifted once, never turn-shift-turn-shift.

    Measured on the device, and it is what the last sessions looked like: the same piece
    rotating, then shifting, then rotating again. Both halves re-measured every frame, and a
    piece's mask moves a column as it turns (the device pivots on its second cell) and the
    split can read it a cell differently — so "not where the plan says" kept flipping, and
    each flip sent the piece back to the rotate button.

    So the phases are monotonic: rotate until the orientation is right (as many taps as it
    takes, bounded by the rotate guard), then one shift (as many taps as the distance needs,
    all on one frame), then nothing at all until the piece lands. A tap the game misses is
    not corrected any more — that is the trade the operator asked for, and it is what makes
    the piece predictable.
    """
    mask = [[1, 1, 1]]
    before = _three_bar(mask, [(2, 3), (2, 4), (2, 5)])
    candidates = candidates_for(simulator, before)
    target = next(
        index for index, (placement, _) in enumerate(candidates) if placement.rotation == 1
    )
    probabilities = [0.01] * len(candidates)
    probabilities[target] = 0.9
    answering(monkeypatch, probabilities)
    jev_policy.reset_decisions()

    turn = jev_policy.decide(before, calibration)
    assert turn.state["phase"] == "rotate", "first the turn"

    # The device turned it: upright, still in the column it started in.
    turned = _three_bar([[1], [1], [1]], [(2, 5), (3, 5), (4, 5)])
    shift = jev_policy.decide(turned, calibration)
    assert shift.state["phase"] == "move", "then the shift, once"
    shift_gestures = jev_policy._last["placement"]

    # Whatever the frames after that show — the same turned piece, or a mask read a column
    # over — the same piece is never turned or moved again.
    for drifted in (
        turned,
        _three_bar([[1], [1], [1]], [(2, 4), (3, 4), (4, 4)]),
        _three_bar(mask, [(2, 3), (2, 4), (2, 5)]),
    ):
        later = jev_policy.decide(drifted, calibration)
        assert later.state.get("phase") == "hold", (
            f"the piece was already turned and shifted; got {later.state.get('phase')}"
        )
        assert later.gestures == (), "nothing may be dispatched after the one shift"
        assert later.state["placement"] == shift_gestures.as_dict()


def test_the_placement_frame_uses_the_column_measured_now(
    jev_policy, simulator, calibration, monkeypatch
):
    """The device pivots on the second cell, so the column it lands in after a turn is not
    the column it started in: the move must be computed from the frame it is executed on."""
    state = _three_bar([[1, 1, 1]], [(2, 3), (2, 4), (2, 5)])
    answering(monkeypatch, [0.9] * len(candidates_for(simulator, state)))
    jev_policy.reset_decisions()

    jev_policy.decide(state, calibration)
    placed = jev_policy.decide(_three_bar([[1], [1], [1]], [(2, 6), (3, 6), (4, 6)]), calibration)

    assert placed.state["current_column"] == 6, "the column that frame showed, not the old one"


def test_the_placement_shifts_once_and_then_leaves_the_piece_alone(
    jev_policy, simulator, calibration, monkeypatch
):
    """One shift per piece, decided from the frame the turn finished on.

    This replaces a test that pinned the opposite: correcting the column on *every* frame
    "because a tap can be missed". The operator watched the device and saw what that
    actually produced — the same piece turning, shifting, turning, shifting — because a
    turn moves the piece's mask a column (the device pivots on its second cell) and a split
    can read it a cell over, so the correction kept flipping sign. One shift, then hold
    until it lands, is both the operator's rule and the fix.
    """
    state = _three_bar([[1, 1, 1]], [(2, 3), (2, 4), (2, 5)])
    candidates = candidates_for(simulator, state)
    target_index = next(
        index
        for index, (placement, _) in enumerate(candidates)
        if (placement.rotation, placement.column) == (0, 4)
    )
    probabilities = [0.01] * len(candidates)
    probabilities[target_index] = 0.9
    answering(monkeypatch, probabilities)
    jev_policy.reset_decisions()

    first = jev_policy.decide(state, calibration)
    assert first.state["placement"] == {"rotation": 0, "column": 4}
    assert first.state["phase"] == "move" and first.state["move_columns"] == 1, (
        "the shift is issued on the frame the decision is made (no turn needed here)"
    )

    # Every frame after it: hold. Including the ones that show the piece somewhere else,
    # which is exactly the reading that used to trigger a correction.
    for drifted in (
        _three_bar([[1, 1, 1]], [(4, 6), (4, 7), (4, 8)]),  # overshot
        _three_bar([[1, 1, 1]], [(6, 2), (6, 3), (6, 4)]),  # fell short
        _three_bar([[1, 1, 1]], [(8, 4), (8, 5), (8, 6)]),  # on target
    ):
        later = jev_policy.decide(drifted, calibration)
        assert later.state["phase"] == "hold"
        assert not later.state.get("move_columns")
        assert later.gestures == ()


def test_a_piece_is_turned_once_even_if_the_mask_keeps_saying_otherwise(
    jev_policy, simulator, calibration, monkeypatch
):
    """After the shift, a reading that says "still needs turning" is not acted on."""
    mask = [[1, 1, 1]]
    before = _three_bar(mask, [(2, 3), (2, 4), (2, 5)])
    candidates = candidates_for(simulator, before)
    target = next(
        index for index, (placement, _) in enumerate(candidates) if placement.rotation == 1
    )
    probabilities = [0.01] * len(candidates)
    probabilities[target] = 0.9
    answering(monkeypatch, probabilities)
    jev_policy.reset_decisions()

    jev_policy.decide(before, calibration)  # turn
    turned = _three_bar([[1], [1], [1]], [(2, 5), (3, 5), (4, 5)])
    jev_policy.decide(turned, calibration)  # shift

    # The flat mask again, as if the split read the piece before the turn.
    again = jev_policy.decide(before, calibration)
    assert again.state["phase"] == "hold"
    assert again.gestures == (), "a piece is turned once, whatever the next frame reads"


def test_the_piece_is_placed_once_per_piece_even_while_correcting(
    jev_policy, simulator, calibration, monkeypatch
):
    asked = []

    def fake_ask(payload, questions, **kwargs):
        asked.append(payload)
        return {f"cand_{index}": {"type": "noul", "noul": 0.5} for index in range(len(questions))}

    monkeypatch.setattr(systemone, "ask", fake_ask)
    jev_policy.reset_decisions()
    for column in (3, 4, 2, 1):
        jev_policy.decide(
            _three_bar([[1, 1, 1]], [(6, column), (6, column + 1), (6, column + 2)]), calibration
        )

    assert len(asked) == 1, "correcting the same piece is not a new decision"
