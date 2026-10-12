"""T3: how many columns the board has — measured, never assumed.

This is the plan's key judgement, and it exists because of a specific failure: on
2026-10-12 the playfield was called 10x20, then "corrected" to 8x16 with a described
counting method, then corrected back — three positions in three turns, and nobody
had measured anything. The observation behind all three was a 1:2 aspect ratio,
which 8x16 and 10x20 share: it has zero power to separate them.

So the check here must not use the assumption under test. It reads the periodic
structure the field already has — the gridlines — takes the spacing between them,
and compares the count with the field's own extent: `columns * pitch == width`. No
count is passed in, and nothing about "a Tetris board is 10 wide" is in the code.

Note what that costs: a field with no regular periodicity measures as `None`. A
menu, an ad banner or a dialog over the board produces edges, but not *periodic*
edges, and this test pins that too — Review Focus 5 in the plan.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import numpy as np
import pytest

from artemis.reflex.registry import load_skill

SKILLS = Path(__file__).resolve().parents[3] / "skills"
BACKGROUND = (26.0, 68.0, 88.0)
GRIDLINE = (140.0, 150.0, 160.0)
BLOCK = (220.0, 40.0, 40.0)
TEXT = (240.0, 240.0, 240.0)
PITCH = 40
LINE = 2


@pytest.fixture(scope="module")
def probe():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.grid_probe")


@pytest.fixture(scope="module")
def roi():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.roi")


@pytest.fixture(scope="module")
def calibration():
    return json.loads((SKILLS / "tetris" / "calibration.json").read_text(encoding="utf-8"))


def board_like(columns, rows, *, pitch=PITCH, filled=()):
    """A field shaped like a playfield: background, cell gridlines, some blocks.

    The caller passes the size it *painted*; the function under test never sees it.
    """
    height, width = rows * pitch, columns * pitch
    field = np.zeros((height, width, 3), dtype="float32")
    field[:, :] = BACKGROUND
    for index in range(columns + 1):
        x = min(index * pitch, width - LINE)
        field[:, x : x + LINE] = GRIDLINE
    for index in range(rows + 1):
        y = min(index * pitch, height - LINE)
        field[y : y + LINE, :] = GRIDLINE
    for row, column in filled:
        field[
            row * pitch + 6 : (row + 1) * pitch - 6,
            column * pitch + 6 : (column + 1) * pitch - 6,
        ] = BLOCK
    return field


def decode(data: bytes):
    """JPEG/PNG bytes → an ``(H, W, 3)`` float array, the way perception reads a frame."""
    import io

    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype="float32")


# --------------------------------------------------------------------------- #
# The measurement
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("columns", "rows"), [(10, 20), (8, 16), (12, 24)])
def test_the_grid_is_measured_from_the_pixels_not_told(probe, columns, rows):
    report = probe.measure_grid(board_like(columns, rows, filled=[(rows - 1, 0), (rows - 2, 3)]))

    assert report["columns"] == columns, report
    assert report["rows"] == rows, report
    assert report["pitch_x"] == PITCH and report["pitch_y"] == PITCH
    assert report["extent_ok"] is True


def test_the_column_count_comes_from_periodicity_not_from_the_aspect_ratio(probe):
    """The incident's non-evidence, reproduced: 8x16 and 10x20 are both 1:2.

    Same shape, same ratio, different answer — only the gridlines can tell them
    apart, which is exactly why the aspect ratio was never evidence.
    """
    eight = board_like(8, 16)
    ten = board_like(10, 20)

    assert eight.shape[0] / eight.shape[1] == ten.shape[0] / ten.shape[1] == 2.0
    assert probe.measure_grid(eight)["columns"] == 8
    assert probe.measure_grid(ten)["columns"] == 10


def test_a_field_that_is_not_an_integer_number_of_cells_fails_the_extent_check(probe):
    field = board_like(10, 20)[:, :-10]  # 390 px wide: 10 x 40 does not fit

    report = probe.measure_grid(field)

    assert report["columns"] == 10
    assert report["extent_ok"] is False


# --------------------------------------------------------------------------- #
# The period itself
# --------------------------------------------------------------------------- #


def profile_with_period(period, *, count=20):
    profile = np.zeros(period * count, dtype="float32")
    profile[::period] = 1.0
    return profile


@pytest.mark.parametrize("period", [12, 20, 25, 40])
def test_the_period_is_the_cell_not_a_pair_of_cells(probe, period):
    """A signal periodic at ``p`` is periodic at ``2p`` too; the cell is the smaller.

    Without that preference a 10-wide board measures as 5 columns of a double-width
    cell — and the extent check would agree, because ``5 x 80 == 400``.
    """
    assert probe.pitch(profile_with_period(period)) == period


def test_a_featureless_profile_has_no_period(probe):
    assert probe.pitch(np.zeros(400, dtype="float32")) is None
    assert probe.pitch(np.random.default_rng(3).random(400).astype("float32")) is None


# --------------------------------------------------------------------------- #
# What is *not* a board
# --------------------------------------------------------------------------- #


def test_a_solid_colour_field_has_no_grid(probe):
    field = np.zeros((800, 400, 3), dtype="float32")
    field[:, :] = BACKGROUND

    assert probe.measure_grid(field)["columns"] is None


def test_ui_text_is_not_a_board(probe):
    """Review Focus 5: an ad or dialog over the playfield must read as 'not a board'.

    Glyph-like blobs give plenty of edges; what they do not give is a *regular*
    spacing, and it is regularity — not the median gap — that a board has.
    """
    field = np.zeros((800, 400, 3), dtype="float32")
    field[:, :] = BACKGROUND
    source = np.random.default_rng(20261012)
    for _ in range(40):
        y, x = source.integers(0, 780), source.integers(0, 380)
        height, width = source.integers(6, 22), source.integers(4, 14)
        field[y : y + height, x : x + width] = TEXT

    report = probe.measure_grid(field)

    assert report["columns"] is None, "text became a grid"
    assert report["extent_ok"] is False


def test_a_photo_like_noise_field_is_not_a_board(probe):
    field = np.random.default_rng(7).integers(0, 255, size=(800, 400, 3)).astype("float32")

    assert probe.measure_grid(field)["columns"] is None


# --------------------------------------------------------------------------- #
# The device's own pixels
# --------------------------------------------------------------------------- #


def test_the_reference_frames_measure_ten_by_twenty(probe, roi, calibration):
    """The corpus is the evidence: the same three real screenshots, measured.

    This is the assertion T2's pixel box was never able to make — it is a
    measurement, not a restatement of the number in calibration.json.
    """
    from artemis_skills_tetris.frames import load_reference_frames

    frames = load_reference_frames()
    assert len(frames) >= 3
    for name, data, _size in frames[:3]:
        field = roi.board_crop(decode(data), calibration)
        report = probe.measure_grid(field)
        assert (report["columns"], report["rows"]) == (10, 20), f"{name}: {report}"
        assert report["extent_ok"] is True, f"{name}: {report}"
