"""The loop's own entry point: a real screen → the state the policies consume.

Every module in this plan is tested in isolation. This file tests that they are
actually *connected*: ``perception.parse`` takes a 720x1600 screenshot and returns a
state carrying the measured grid, the board matrix, the piece's geometry and its
motion — the keys ``policy_jev`` reads. Without this, the plan would ship four modules
and a Jev policy that nothing calls.

Two frames are used on purpose: the falling piece is separated by motion, so one frame
cannot show one (that is ``pieces.py``'s documented answer), and a whole chain that
only works on a single frame would be a chain that never finds a piece.

The synthetic screens are drawn from ``calibration.json``'s ratios, at the device's own
resolution and cell pitch, so the path exercised is the path the device exercises.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import numpy as np
import pytest

from artemis.reflex.registry import load_skill

SKILLS = Path(__file__).resolve().parents[3] / "skills"
SCREEN = (1600, 720)  # (height, width) of a PEMM00 screenshot
BOX = (35, 320, 465, 1180)  # the measured playfield box, in device pixels
CELL = 43
BACKGROUND = (26.0, 68.0, 88.0)
GRIDLINE = (60.0, 90.0, 110.0)
BLOCK = (220.0, 40.0, 40.0)
STACK = [(19, 0), (19, 1), (19, 2), (18, 0)]
PIECE_HIGH = [(5, 3), (5, 4), (5, 5), (6, 3)]
PIECE_LOW = [(6, 3), (6, 4), (6, 5), (7, 3)]


@pytest.fixture(scope="module")
def perception():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.perception")


@pytest.fixture(scope="module")
def calibration():
    return json.loads((SKILLS / "tetris" / "calibration.json").read_text(encoding="utf-8"))


def screen_with(filled):
    """A 720x1600 screen showing the board: gridlines everywhere, blocks where given."""
    image = np.zeros((*SCREEN, 3), dtype="float32")
    image[:, :] = BACKGROUND
    x0, y0, x1, y1 = BOX
    for index in range(11):
        x = min(x0 + index * CELL, x1 - 2)
        image[y0:y1, x : x + 2] = GRIDLINE
    for index in range(21):
        y = min(y0 + index * CELL, y1 - 2)
        image[y : y + 2, x0:x1] = GRIDLINE
    for row, column in filled:
        image[
            y0 + row * CELL + 3 : y0 + (row + 1) * CELL - 3,
            x0 + column * CELL + 3 : x0 + (column + 1) * CELL - 3,
        ] = BLOCK
    return image


class Frame:
    """What the runtime hands a skill: a frame with image bytes on it."""

    def __init__(self, image):
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.fromarray(image.astype("uint8"), mode="RGB").save(buffer, format="JPEG", quality=95)
        self.image = buffer.getvalue()


# --------------------------------------------------------------------------- #
# A screen becomes a state two policies can read
# --------------------------------------------------------------------------- #


def test_a_screen_becomes_the_state_the_jev_policy_reads(perception, calibration):
    perception.reset_history()
    perception.parse(Frame(screen_with(STACK + PIECE_HIGH)), calibration)

    state = perception.parse(Frame(screen_with(STACK + PIECE_LOW)), calibration)

    assert state is not None
    for key in ("board_matrix", "current_piece_mask", "current_piece_positions", "motion_vector"):
        assert key in state, key
    assert len(state["board_matrix"]) == 20
    assert all(len(row) == 10 for row in state["board_matrix"])
    assert set("".join(state["board_matrix"])) <= {"0", "1"}
    assert state["current_piece_positions"] == [list(cell) for cell in PIECE_LOW]
    assert state["motion_vector"] == [1, 0]
    assert state["current_piece_mask"] == [[1, 1, 1], [1, 0, 0]]


def test_the_piece_is_removed_from_the_terrain(perception, calibration):
    perception.reset_history()
    perception.parse(Frame(screen_with(STACK + PIECE_HIGH)), calibration)

    state = perception.parse(Frame(screen_with(STACK + PIECE_LOW)), calibration)

    occupied = {
        (row, column)
        for row, line in enumerate(state["board_matrix"])
        for column, cell in enumerate(line)
        if cell == "1"
    }
    assert occupied == set(STACK), "the board is the stack, not the stack plus the piece"


def test_the_grid_is_measured_and_reported_in_screen_pixels(perception, calibration):
    """``control.py`` computes swipe coordinates from this, so it must be device pixels."""
    perception.reset_history()

    state = perception.parse(Frame(screen_with(STACK)), calibration)

    assert state["grid"] == {
        "origin_x": BOX[0],
        "origin_y": BOX[1],
        "cell_w": CELL,
        "cell_h": CELL,
        "cols": 10,
        "rows": 20,
    }


def test_a_state_carries_a_name_for_a_standard_piece_only(perception, calibration):
    """The letter-based search still needs a name; a custom shape has none."""
    perception.reset_history()
    perception.parse(Frame(screen_with(STACK + PIECE_HIGH)), calibration)

    state = perception.parse(Frame(screen_with(STACK + PIECE_LOW)), calibration)

    assert state["current"] in {"L", "J"}, "the painted piece is an L/J"
    assert state["board"][19][0] != ".", "the legacy board is filled too"


def test_a_custom_shape_has_no_letter_and_is_still_playable(perception, calibration):
    """Review Focus 4: geometry travels; the letter is only a convenience."""
    block = [(6, 6), (6, 7), (7, 6), (7, 7), (8, 6), (8, 7)]
    lower = [(row + 1, column) for row, column in block]
    perception.reset_history()
    perception.parse(Frame(screen_with(STACK + block)), calibration)

    state = perception.parse(Frame(screen_with(STACK + lower)), calibration)

    assert state["current"] is None
    assert state["current_piece_mask"] == [[1, 1], [1, 1], [1, 1]]
    assert state["motion_vector"] == [1, 0]


# --------------------------------------------------------------------------- #
# What is not a board
# --------------------------------------------------------------------------- #


def test_something_covering_the_board_reads_as_blind(perception, calibration):
    """Review Focus 5: an ad or dialog must stop the loop, not become a stack."""
    perception.reset_history()
    source = np.random.default_rng(20261012)
    image = np.zeros((*SCREEN, 3), dtype="float32")
    image[:, :] = BACKGROUND
    for _ in range(200):
        y, x = source.integers(0, 1560), source.integers(0, 700)
        image[y : y + source.integers(8, 30), x : x + source.integers(6, 20)] = (
            240.0,
            240.0,
            240.0,
        )

    assert perception.parse(Frame(image), calibration) is None


def test_the_older_calibration_still_works(perception, calibration):
    """A skill calibrated before the ratios existed keeps its old path."""
    legacy = {key: value for key, value in calibration.items() if key != "board_roi_ratios"}
    perception.reset_history()

    state = perception.parse(Frame(screen_with(STACK)), legacy)

    assert state is not None
    assert "board" in state and "grid" in state
    assert state["grid"]["cols"] == 10
