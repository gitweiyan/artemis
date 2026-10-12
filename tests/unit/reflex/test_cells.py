"""T4: one cell, one bit — the HSV median of its centre.

Three choices, each answering a way the reading goes wrong:

* **centre 5x5, not the whole cell** — a cell's border is where the glow and the
  gridline live, and it is the noisiest part of the cell; the centre is the part that
  is entirely the block's own colour;
* **median, not a single pixel** — blocks have anti-aliased, glowing edges (Review
  Focus 1): a point sample on such an edge flips the cell's reading frame by frame;
* **saturation as well as value** — the field is a deep blue theme whose own
  background is saturated (S ~ 70), so saturation alone calls every empty cell a
  block, and brightness alone calls white UI text a block: white text is bright
  (V ~ 94) but has no saturation at all. Both thresholds, or the classifier reads a
  dialog as a stack (Review Focus 5).

The thresholds are calibration, not code: a different game, a different theme.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import numpy as np
import pytest

from artemis.reflex.registry import load_skill

SKILLS = Path(__file__).resolve().parents[3] / "skills"
FIELD = (800, 400, 3)
BACKGROUND = (26.0, 68.0, 88.0)
#: One block colour, the incident's own: (220, 40, 40).
BLOCK = (220.0, 40.0, 40.0)
CELL = 40


@pytest.fixture(scope="module")
def cells():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.cells")


@pytest.fixture(scope="module")
def calibration():
    return json.loads((SKILLS / "tetris" / "calibration.json").read_text(encoding="utf-8"))


def board_field():
    field = np.zeros(FIELD, dtype="float32")
    field[:, :] = BACKGROUND
    return field


def cell_box(row, column, *, inset=0):
    y0, x0 = row * CELL + inset, column * CELL + inset
    return slice(y0, (row + 1) * CELL - inset), slice(x0, (column + 1) * CELL - inset)


def paint_cell(field, row, column, rgb, *, inset=0):
    rows, columns = cell_box(row, column, inset=inset)
    field[rows, columns] = rgb


# --------------------------------------------------------------------------- #
# The matrix
# --------------------------------------------------------------------------- #


def test_the_matrix_is_exactly_what_the_pixels_show(cells, calibration):
    colours = list(calibration["colours"].values())
    assert len(colours) == 7, "the palette this test paints with"
    placed = {
        (0, 0): colours[0],
        (1, 3): colours[1],
        (5, 1): colours[2],
        (7, 9): colours[3],
        (12, 4): colours[4],
        (19, 0): colours[5],
        (19, 9): colours[6],
    }
    field = board_field()
    for (row, column), rgb in placed.items():
        paint_cell(field, row, column, rgb, inset=6)  # a block inside its own cell

    matrix = cells.read_matrix(field, calibration)

    expected = ["0" * 10 for _ in range(20)]
    for row, column in placed:
        expected[row] = expected[row][:column] + "1" + expected[row][column + 1 :]
    assert matrix == expected


def test_a_few_pixels_in_a_corner_are_not_a_block(cells, calibration):
    """A colour in a cell's corner is somebody else's pixel — often the next cell's block.

    The verdict is a *fraction* of the cell, so a corner that would have been "the centre
    sample was elsewhere" is now simply too small a share to count.
    """
    field = board_field()
    paint_cell(field, 2, 2, BLOCK, inset=8)  # most of the cell
    field[4 * CELL : 4 * CELL + 12, 4 * CELL : 4 * CELL + 12] = BLOCK  # 9% of the cell

    matrix = cells.read_matrix(field, calibration)

    assert matrix[2][2] == "1"
    assert matrix[4][4] == "0", "a ninth of a cell is not a block"


def test_every_cell_reports_its_own_hsv_for_review(cells, calibration):
    field = board_field()
    paint_cell(field, 3, 7, BLOCK, inset=6)

    hsv = cells.cell_hsv(field, calibration)

    assert len(hsv) == 20 and all(len(row) == 10 for row in hsv)
    assert len(hsv[3][7]) == 3
    _hue, saturation, value = hsv[3][7]
    assert value > 80 and saturation > 60, "the block is bright and saturated"
    assert hsv[0][0][2] < 40, "an empty cell is dark"


# --------------------------------------------------------------------------- #
# What must never become a block
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("rgb", "why"),
    [
        ((240.0, 240.0, 240.0), "white UI text: bright, unsaturated"),
        ((150.0, 150.0, 150.0), "grey UI chrome"),
        ((60.0, 40.0, 30.0), "below the value threshold"),
        ((26.0, 68.0, 88.0), "the empty field itself, which is saturated (S~70)"),
    ],
)
def test_a_cell_below_the_thresholds_is_never_a_block(cells, calibration, rgb, why):
    field = board_field()
    paint_cell(field, 10, 5, rgb)

    assert cells.read_matrix(field, calibration)[10][5] == "0", why


def test_the_thresholds_live_in_the_calibration_not_the_code(cells):
    """No thresholds, no verdicts: an uncalibrated classifier must refuse, not guess."""
    field = board_field()
    paint_cell(field, 10, 5, BLOCK)

    with pytest.raises(ValueError, match="hsv"):
        cells.read_matrix(field, {"grid": {"cols": 10, "rows": 20}})


# --------------------------------------------------------------------------- #
# A block between two rows is still a block (fixed after the device session)
# --------------------------------------------------------------------------- #

#: What a cell's pixels look like when the game is animating a piece between rows: the
#: device's blocks are 39 px on a 42.5 px pitch, so a piece one third of the way between
#: two rows covers about 40% of each cell.
STRADDLE = 18


def test_a_block_straddling_a_row_boundary_is_still_a_block(cells, calibration):
    """
    中心 5x5 采样会两格都判空；整格占用率判据能看到那 40%。
    """
    field = board_field()
    field[3 * CELL - STRADDLE : 3 * CELL + STRADDLE, 5 * CELL + 2 : 6 * CELL - 2] = BLOCK

    matrix = cells.read_matrix(field, calibration)

    assert matrix[2][5] == "1" and matrix[3][5] == "1", "the block is in both cells' pixels"
    assert matrix[2][4] == "0" and matrix[3][4] == "0", "and in no other cell"


def test_a_cell_that_is_mostly_glow_is_not_a_block(cells, calibration):
    """The field's own glow sits just below the threshold; a fifth of a cell must not count."""
    field = board_field()
    paint_cell(field, 10, 3, BLOCK, inset=32)  # a 8x8 patch: far below the occupancy cut

    assert cells.read_matrix(field, calibration)[10][3] == "0"


def test_the_occupancy_threshold_lives_in_the_calibration(cells, calibration):
    field = board_field()
    field[3 * CELL - STRADDLE : 3 * CELL + STRADDLE, 5 * CELL + 2 : 6 * CELL - 2] = BLOCK

    strict = {**calibration, "hsv": {**calibration["hsv"], "min_occupancy": 0.9}}
    lenient = {**calibration, "hsv": {**calibration["hsv"], "min_occupancy": 0.1}}

    assert cells.read_matrix(field, strict)[2][5] == "0"
    assert cells.read_matrix(field, lenient)[2][5] == "1"
