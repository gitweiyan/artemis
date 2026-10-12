"""T2: the board is a *proportion* of the screen, and the crop is normalised.

Why a ratio and not the measured pixel box: ``(35, 320, 465, 1180)`` is one
device's answer at one resolution. A ratio survives a different phone, a different
density, a letterboxed screenshot; an absolute box silently crops the wrong place
the moment any of those changes.

Why normalise at all: every later stage (grid counting, cell classification, piece
separation) then works on one fixed geometry, so its numbers are comparable across
frames — and 430x860 -> 400x800 puts the cell pitch at exactly 40 px.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import numpy as np
import pytest

from artemis.reflex.registry import load_skill

SKILLS = Path(__file__).resolve().parents[3] / "skills"
#: (height, width) of a PEMM00 screenshot.
SCREEN = (1600, 720)
MARKER = (220.0, 40.0, 40.0)
#: The real box on a 720x1600 PEMM00 screenshot, measured from the frame's own edges
#: (2026-10-12: the outer background plateaus at brightness 47.3 and steps to >=67 inside
#: the board at x=30, x=450, y=380, y=1235). It replaced x[35,465] y[320,1180], which was
#: 60 px high in y - about 1.4 rows - and came from a descriptive answer, not a measurement.
MEASURED_BOX = (30, 380, 450, 1235)
#: Cell size in screen pixels, from the same measurement (10 and 20 cells across the box).
CELL_W, CELL_H = 42.0, 42.75
NORMALISED = (800, 400, 3)


@pytest.fixture(scope="module")
def roi():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.roi")


@pytest.fixture(scope="module")
def calibration():
    return json.loads((SKILLS / "tetris" / "calibration.json").read_text(encoding="utf-8"))


def test_the_calibrated_ratio_lands_on_the_measured_pixel_box(roi, calibration):
    assert roi.roi_pixels(SCREEN, calibration) == MEASURED_BOX


def test_the_crop_is_the_normalised_board_with_the_pattern_in_its_own_cell(roi, calibration):
    # A marker painted inside one known board cell of a 720x1600 screen: row 2,
    # column 3 of the measured 10x20 grid.
    row, column = 2, 3
    x0, y0, _x1, _y1 = MEASURED_BOX
    image = np.zeros((*SCREEN, 3), dtype="float32")
    inset = 5  # more than half the cell, so the cell's median is the marker
    image[
        round(y0 + row * CELL_H + inset) : round(y0 + (row + 1) * CELL_H - inset),
        round(x0 + column * CELL_W + inset) : round(x0 + (column + 1) * CELL_W - inset),
    ] = MARKER

    crop = roi.board_crop(image, calibration)

    assert crop.shape == NORMALISED
    assert crop.dtype == np.float32
    cell = crop[row * 40 : (row + 1) * 40, column * 40 : (column + 1) * 40]
    assert np.allclose(np.median(cell.reshape(-1, 3), axis=0), MARKER), (
        "the marker must land in the board cell it was painted in"
    )
    neighbour = crop[(row + 1) * 40 : (row + 2) * 40, column * 40 : (column + 1) * 40]
    assert not np.allclose(np.median(neighbour.reshape(-1, 3), axis=0), MARKER), (
        "the normalised cell must be 40 px, not a stretched screen cell"
    )


def test_the_crop_covers_the_whole_roi(roi, calibration):
    """A marker in the last row must survive: a half-open box would clip it."""
    _x0, _y0, x1, y1 = MEASURED_BOX
    image = np.zeros((*SCREEN, 3), dtype="float32")
    image[y1 - 10 : y1, x1 - 10 : x1] = MARKER

    crop = roi.board_crop(image, calibration)

    assert np.allclose(crop[-1, -1], MARKER)


def test_ratios_outside_the_image_are_clamped_not_wrapped(roi):
    calibration = {"board_roi_ratios": [-0.2, 0.2, 1.4, 1.2], "normalized_size": [400, 800]}

    assert roi.roi_pixels(SCREEN, calibration) == (0, 320, 720, 1600)


def test_an_uncalibrated_ratio_is_an_error_not_a_whole_screen_crop(roi):
    """Cropping the whole screen would classify menus and ads as board cells."""
    with pytest.raises(ValueError, match="board_roi_ratios"):
        roi.roi_pixels(SCREEN, {})
