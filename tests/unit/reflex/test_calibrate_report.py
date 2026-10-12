"""T9: what a calibration run must show before anyone trusts the numbers.

A calibration is only as good as the reader's ability to see it fail. Two things this
report has to make visible:

* **the grid, measured against its own extent** — "10 columns" is a claim; "10 columns
  of 40 px in a 400 px field, extent closed" is a measurement, and the difference is
  the entire 2026-10-12 incident;
* **each cell's own HSV next to the thresholds** — a cell that reads as empty because
  it *just* missed the threshold is the cell that will misread next time the light
  changes, and it is invisible in a matrix of 0s and 1s.

The command's exit code carries the same judgement, so a script can refuse to trust a
run: a grid that does not fit its extent is not a calibration.
"""

from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from typer.testing import CliRunner

from artemis.interfaces.cli.commands import reflex as reflex_command
from artemis.reflex.registry import load_skill

SKILLS = Path(__file__).resolve().parents[3] / "skills"
SCREEN = (1600, 720)
#: The measured playfield box and cell size on a 720x1600 PEMM00 screenshot.
BOX = (30, 380, 450, 1235)
CELL_W, CELL_H = 42.0, 42.75
BACKGROUND = (26.0, 68.0, 88.0)
GRIDLINE = (60.0, 90.0, 110.0)
BLOCK = (220.0, 40.0, 40.0)
#: Bright enough to look like a block, not bright enough to pass (V = 62.7 < 65).
NEARLY = (160.0, 40.0, 40.0)
STACK = [(19, 0), (19, 1), (19, 2), (18, 0)]
PIECE_HIGH = [(5, 3), (5, 4), (5, 5), (6, 3)]
PIECE_LOW = [(6, 3), (6, 4), (6, 5), (7, 3)]


@pytest.fixture(scope="module")
def calibrate():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.calibrate")


@pytest.fixture(scope="module")
def calibration():
    return json.loads((SKILLS / "tetris" / "calibration.json").read_text(encoding="utf-8"))


def screen_with(filled, *, extra=None):
    """A 720x1600 screen showing the board: gridlines everywhere, blocks where given."""
    image = np.zeros((*SCREEN, 3), dtype="float32")
    image[:, :] = BACKGROUND
    x0, y0, x1, y1 = BOX
    for index in range(11):
        x = min(round(x0 + index * CELL_W), x1 - 2)
        image[y0:y1, x : x + 2] = GRIDLINE
    for index in range(21):
        y = min(round(y0 + index * CELL_H), y1 - 2)
        image[y : y + 2, x0:x1] = GRIDLINE
    for row, column in filled:
        paint(image, row, column, BLOCK)
    for (row, column), colour in (extra or {}).items():
        paint(image, row, column, colour)
    return image


def paint(image, row, column, colour):
    x0, y0, _, _ = BOX
    image[
        round(y0 + row * CELL_H + 3) : round(y0 + (row + 1) * CELL_H - 3),
        round(x0 + column * CELL_W + 3) : round(x0 + (column + 1) * CELL_W - 3),
    ] = colour


def encode(image) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(image.astype("uint8"), mode="RGB").save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def reference_bytes():
    return [data for _name, data, _size in _reference_frames()]


def _reference_frames():
    frames = importlib.import_module("artemis_skills_tetris.frames")
    return frames.load_reference_frames()


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def test_a_real_frame_verifies_as_a_ten_by_twenty_board(calibrate, calibration):
    for data in reference_bytes():
        report = calibrate.verify_report(data, calibration)

        assert report["grid"] == {"columns": 10, "rows": 20, "extent_ok": True}
        assert len(report["matrix"]) == 20
        assert all(len(row) == 10 for row in report["matrix"])
        assert len(report["cell_hsv"]) == 20 and len(report["cell_hsv"][0]) == 10
        assert all(len(cell) == 3 for cell in report["cell_hsv"][0])
        assert report["motion_vector"] is None, "one frame cannot show motion"
        assert report["suspected_sleep_screen"] is False


def test_a_black_frame_is_flagged_as_a_sleep_screen(calibrate, calibration):
    """The device sleeps; a black frame is not a calibration, it is a dark screen."""
    report = calibrate.verify_report(encode(np.zeros((*SCREEN, 3), dtype="float32")), calibration)

    assert report["suspected_sleep_screen"] is True
    assert report["grid"]["extent_ok"] is False


def test_motion_is_reported_when_a_history_is_given(calibrate, calibration):
    """The plan's single-image signature, plus the sequence that makes motion possible."""
    cells = importlib.import_module("artemis_skills_tetris.cells")
    roi = importlib.import_module("artemis_skills_tetris.roi")
    from PIL import Image

    def matrix_of(image_bytes):
        array = np.asarray(Image.open(io.BytesIO(image_bytes)).convert("RGB"), dtype="float32")
        return cells.read_matrix(roi.board_crop(array, calibration), calibration)

    earlier = matrix_of(encode(screen_with(STACK + PIECE_HIGH)))
    report = calibrate.verify_report(
        encode(screen_with(STACK + PIECE_LOW)), calibration, history=[earlier]
    )

    assert report["motion_vector"] == [1, 0]
    assert report["matrix"] != earlier


# --------------------------------------------------------------------------- #
# The per-cell review
# --------------------------------------------------------------------------- #


def test_the_review_names_the_cells_that_only_just_missed_the_threshold(calibrate, calibration):
    """A cell at V=62.7 with v_min=65 is the cell that will flip when the light moves."""
    image = screen_with(STACK, extra={(10, 2): NEARLY, (10, 6): BLOCK})

    report = calibrate.verify_report(encode(image), calibration)
    rendered = calibrate.render_verification(report, calibration)

    assert report["matrix"][10][2] == "0" and report["matrix"][10][6] == "1"
    borderline = rendered.split("near the threshold")[1]
    assert "row 10, column 2" in borderline, rendered
    assert "row 10, column 6" not in borderline, "a cell that read as filled is not borderline"


def test_the_review_prints_the_measured_grid_and_its_extent(calibrate, calibration):
    report = calibrate.verify_report(reference_bytes()[0], calibration)

    rendered = calibrate.render_verification(report, calibration)

    assert "10 x 20" in rendered or "10x20" in rendered
    assert "extent" in rendered.lower()


# --------------------------------------------------------------------------- #
# The command's exit code
# --------------------------------------------------------------------------- #


def fake_capture(image_bytes, monkeypatch):
    class Source:
        def grab(self):
            return SimpleNamespace(image=image_bytes, capture_ms=1.0, captured_at=0.0)

        def close(self):
            return None

    monkeypatch.setattr(reflex_command, "_resolve_serial", lambda: "FAKE")
    monkeypatch.setattr(reflex_command, "default_capture", lambda target, prefer="helper": Source())


def run_calibrate(monkeypatch, tmp_path, calibration, image_bytes):
    # The command imports the skill's calibrate module *by name*
    # (``artemis_skills_tetris.calibrate``), so the package has to be registered before
    # the handle is faked — otherwise this test only passes when some other test in the
    # session happened to load the skill first.
    load_skill("tetris", directory=SKILLS)
    handle = SimpleNamespace(
        directory=tmp_path,
        arena=None,
        calibration={key: value for key, value in calibration.items() if key != "package"},
    )
    monkeypatch.setattr(reflex_command.registry, "load_skill", lambda name, **kwargs: handle)
    fake_capture(image_bytes, monkeypatch)
    runner = CliRunner()
    return runner.invoke(
        reflex_command.reflex_app,
        ["calibrate", "--skill", "tetris", "--verify", "--no-keep", "--frames", "1"],
    )


def test_the_command_exits_non_zero_when_the_grid_does_not_fit(monkeypatch, tmp_path, calibration):
    source = np.random.default_rng(20261012)
    noise = source.integers(0, 255, size=(*SCREEN, 3)).astype("float32")

    result = run_calibrate(monkeypatch, tmp_path, calibration, encode(noise))

    assert result.exit_code != 0, result.output
    assert "not a board" in result.output or "extent" in result.output.lower()


def test_the_command_accepts_a_board_that_measures_and_fits(monkeypatch, tmp_path, calibration):
    result = run_calibrate(monkeypatch, tmp_path, calibration, encode(screen_with(STACK)))

    assert result.exit_code == 0, result.output
    assert "10" in result.output and "20" in result.output


def textured_screen(pitch):
    """A full-screen periodic texture — what a UI screen looks like to the measurement."""
    image = np.zeros((*SCREEN, 3), dtype="float32")
    image[:, :] = BACKGROUND
    for x in range(0, SCREEN[1], pitch):
        image[:, x : x + 1] = GRIDLINE
    for y in range(0, SCREEN[0], pitch):
        image[y : y + 1, :] = (90.0, 120.0, 140.0)
    return image


def off_pitch_screen(pitch):
    """Gridlines at a pitch that does not divide the field: extent_ok must fail."""
    image = np.zeros((*SCREEN, 3), dtype="float32")
    image[:, :] = BACKGROUND
    x0, y0, x1, y1 = BOX
    for index in range(10):
        x = min(x0 + index * pitch, x1 - 2)
        image[y0:y1, x : x + 2] = GRIDLINE
    for index in range(19):
        y = min(y0 + index * pitch, y1 - 2)
        image[y : y + 2, x0:x1] = GRIDLINE
    return image


def test_the_review_says_why_a_frame_is_not_this_board(calibrate, calibration):
    """A grid that closes its own extent can still be the wrong board."""
    report = calibrate.verify_report(encode(textured_screen(9)), calibration)

    rendered = calibrate.render_verification(report, calibration)

    assert "NOT THIS BOARD" in rendered, rendered
    assert "declares" in rendered or "divide" in rendered, rendered


def test_the_command_rejects_a_grid_that_does_not_divide_the_field(
    monkeypatch, tmp_path, calibration
):
    """Review Focus 5's stop, at the command level: a pitch that misses must fail the run."""
    result = run_calibrate(monkeypatch, tmp_path, calibration, encode(off_pitch_screen(48)))

    assert result.exit_code != 0, result.output
    assert "extent" in result.output.lower() or "divide" in result.output.lower(), result.output


def test_the_command_rejects_a_board_of_the_wrong_size(monkeypatch, tmp_path, calibration):
    result = run_calibrate(monkeypatch, tmp_path, calibration, encode(textured_screen(9)))

    assert result.exit_code != 0, result.output
    assert "NOT THIS BOARD" in result.output, result.output
