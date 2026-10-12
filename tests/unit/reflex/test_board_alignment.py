"""Real-frame regression: where the board actually is, and what that changes.

The calibration in ``calibration.json`` was measured once, on 2026-10-10, from a
*descriptive* answer rather than from a measurement of the board's own edges — and it was
wrong by 60 px in y, about 1.4 rows. Two consequences, both measured on the frames below:

* every reading was shifted down-left by ~1.4 rows, and the true floor row was never
  sampled at all;
* the falling piece in ``session-0001``→``session-0002`` was reported as *no piece*,
  because its cells straddled the shifted grid.

These frames are real device screenshots, committed as a corpus (like the T1 reference
frames) so the numbers cannot drift back. The expectations below are the readings those
frames must produce — the truth behind them was measured from the pixels: 39x39 px blocks
on a 42.5 px pitch, the bottom-most block's edge at y=1234 against a declared board bottom
of 1235.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from artemis.reflex.registry import load_skill

SKILLS = Path(__file__).resolve().parents[3] / "skills"
SAMPLES = SKILLS / "tetris" / "samples"
#: The board's real box on a 720x1600 PEMM00 screenshot, measured from the frame's own
#: edges (the outer background plateaus at 47.3 brightness on every side).
MEASURED_BOX = (30, 380, 450, 1235)
#: What the frames must read, cell by cell. Measured with both judges (centre 5x5 and
#: whole-cell occupancy), which agree on these frames.
SESSION_0001 = {
    1: "0001110000",
    16: "0000100000",
    17: "0000100000",
    18: "0000110000",
    19: "0000100000",
}
SESSION_0002 = {
    2: "0001110000",
    16: "0000100000",
    17: "0000100000",
    18: "0000110000",
    19: "0000100000",
}
SESSION_0021 = {
    10: "0000110000",
    11: "0000110000",
    15: "0001110000",
    16: "0000100000",
    17: "0000100000",
    18: "0000110000",
    19: "0000100000",
}


@pytest.fixture(scope="module")
def modules():
    load_skill("tetris", directory=SKILLS)
    return {
        name: importlib.import_module(f"artemis_skills_tetris.{name}")
        for name in ("roi", "grid_probe", "cells", "pieces", "perception")
    }


@pytest.fixture(scope="module")
def calibration():
    return json.loads((SAMPLES.parent / "calibration.json").read_text(encoding="utf-8"))


def field_of(modules, calibration, name):
    array = np.asarray(Image.open(SAMPLES / name).convert("RGB"), dtype="float32")
    return modules["roi"].board_crop(array, calibration)


def as_rows(matrix):
    return {row: line for row, line in enumerate(matrix) if "1" in line}


# --------------------------------------------------------------------------- #
# The box itself
# --------------------------------------------------------------------------- #


def test_the_calibrated_box_is_the_board_measured_from_its_own_edges(modules, calibration):
    assert modules["roi"].roi_pixels((1600, 720), calibration) == MEASURED_BOX


def test_the_declared_cell_size_is_the_measured_one(modules, calibration):
    x0, y0, x1, y1 = MEASURED_BOX
    grid = calibration["grid"]

    assert grid["cols"] == 10 and grid["rows"] == 20
    assert grid["origin_x"] == x0 and grid["origin_y"] == y0
    assert grid["cell_w"] == pytest.approx((x1 - x0) / 10, abs=0.01)
    assert grid["cell_h"] == pytest.approx((y1 - y0) / 20, abs=0.01)


# --------------------------------------------------------------------------- #
# The reading
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("session-0001.png", SESSION_0001),
        ("session-0002.png", SESSION_0002),
        ("session-0021.png", SESSION_0021),
    ],
)
def test_a_real_frame_reads_the_cells_the_pixels_show(modules, calibration, name, expected):
    matrix = modules["cells"].read_matrix(field_of(modules, calibration, name), calibration)

    assert as_rows(matrix) == expected


def test_the_stack_reaches_the_floor_row(modules, calibration):
    """The old box stopped 55 px short of the bottom, so row 19 was never the floor."""
    matrix = modules["cells"].read_matrix(
        field_of(modules, calibration, "session-0002.png"), calibration
    )

    assert matrix[19][4] == "1", "the bottom row is the row the stack rests on"


def test_the_measured_grid_is_the_declared_grid_not_a_harmonic(modules, calibration):
    """With the corrected box, the 80 px period is stronger than the 40 px one.

    An unconstrained pitch search therefore locks onto the double period and reports
    5 x 20 — a grid that fails the identity check, so the loop goes blind on a perfectly
    good board. The calibration declares 10 x 20, so the pitch is not a free parameter.
    """
    report = modules["grid_probe"].measure_grid(
        field_of(modules, calibration, "session-0001.png"), calibration
    )

    assert (report["columns"], report["rows"]) == (10, 20), report
    assert report["extent_ok"] is True, report


# --------------------------------------------------------------------------- #
# The falling piece
# --------------------------------------------------------------------------- #


def test_the_falling_piece_the_old_box_missed_is_detected(modules, calibration):
    """Measured: the piece sits in rows 1-2 and touches nothing; the old grid straddled it."""
    first = modules["cells"].read_matrix(
        field_of(modules, calibration, "session-0001.png"), calibration
    )
    second = modules["cells"].read_matrix(
        field_of(modules, calibration, "session-0002.png"), calibration
    )

    split = modules["pieces"].split_pieces([first, second])

    assert split["current_piece_positions"] == [[2, 3], [2, 4], [2, 5]]
    assert split["current_piece_mask"] == [[1, 1, 1]]
    assert split["motion_vector"] == [1, 0], "the piece fell one row between the two frames"


def test_the_loop_returns_a_state_for_these_frames(modules, calibration):
    """End to end through the runtime's own entry point, not just the pieces."""
    perception = modules["perception"]
    perception.reset_history()

    class Frame:
        def __init__(self, path):
            self.image = Path(path).read_bytes()

    perception.parse(Frame(SAMPLES / "session-0001.png"), calibration)
    state = perception.parse(Frame(SAMPLES / "session-0002.png"), calibration)

    assert state is not None, "the frame is a board: the loop must not treat it as blind"
    assert state["current_piece_mask"] == [[1, 1, 1]]
    assert state["motion_vector"] == [1, 0]
    assert state["grid"]["origin_x"] == 30 and state["grid"]["origin_y"] == 380
