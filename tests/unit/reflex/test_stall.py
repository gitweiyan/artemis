"""A board that stops changing: the end of a round, measured instead of guessed.

The operator's rule: the inner loop keeps looking at the playfield, and when it has not
changed for a while the round is over — the outer loop confirms it, restarts, and the
inner loop starts again. The idea is sound; the numbers in the first proposal are not,
and this file is where that was found out.

**The measurement.** ``captures/20261012T061819Z-passive`` is 43 frames of 30.0 s in which
the game *was being played* (the board filled from 4 to 15 cells). Over five-second
windows of that session the playfield is 93.0 %–98.2 % similar — so "similarity > 95 %"
would call the round over **25 times out of 35 windows while it was still being played**.
The reason is arithmetic: the playfield is 359 100 pixels and a tetromino covers ~6 000 of
them, so a piece moving one row changes ~2 % of the frame.

What does separate the two cases is the *size* of the change, once brightness is divided
out (a modal scrim dimmed the whole screen to 0.61x on the device, and a plain difference
would read that as a completely new frame):

    a played board, worst 5 s window      0.982
    the game-over panel, two captures     0.999  (one of them dimmed 0.61x)

so the threshold is **0.995**, which is the middle of that gap rather than a round number.
The committed corpus below is nine real frames (their true capture times kept in
``manifest.json``) so the margin cannot silently rot.

The playfield is compared downsampled 4x: 16x less memory for the same verdict (0.9823 vs
0.9823 on the worst window of the session).
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
TETRIS = SKILLS / "tetris"
SAMPLES = TETRIS / "samples"
PLAY = SAMPLES / "session-play"


@pytest.fixture(scope="module")
def stall():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.stall")


@pytest.fixture(scope="module")
def perception():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.perception")


@pytest.fixture(scope="module")
def calibration():
    return json.loads((TETRIS / "calibration.json").read_text(encoding="utf-8"))


def play_manifest() -> dict:
    return json.loads((PLAY / "manifest.json").read_text(encoding="utf-8"))


def playfield(name: str, *, directory: Path = PLAY) -> np.ndarray:
    """One committed playfield sample as a float array (the samples are already cropped)."""
    return np.asarray(Image.open(directory / name).convert("RGB"), dtype="float32")


#: The committed samples are the playfield itself, so the whole image is the ROI.
WHOLE = {"board_roi_ratios": [0.0, 0.0, 1.0, 1.0]}


def drive(stall, names, times, calibration=None, **overrides):
    """Feed the tracker a series of samples and return the readings it produced."""
    # The override goes last: the committed samples are already the playfield, so the
    # calibration's own board_roi_ratios would crop them a second time.
    settings = {**(calibration or {}), "board_roi_ratios": [0.0, 0.0, 1.0, 1.0]}
    tracker = stall.StallTracker(**{**stall.settings(settings), **overrides})
    readings = []
    for name, when in zip(names, times, strict=True):
        readings.append(tracker.push(when, stall.board_roi(playfield(name), settings)))
    return readings


# --------------------------------------------------------------------------- #
# The measured margin: a played board is not a stalled one
# --------------------------------------------------------------------------- #


def test_a_played_board_never_reads_as_stalled(stall, calibration):
    """Real play, real capture times: the detector must not fire even once."""
    manifest = play_manifest()
    names = [frame["file"] for frame in manifest["frames"]]
    times = [frame["t_mono"] for frame in manifest["frames"]]
    readings = drive(stall, names, times, calibration)

    assert [r["stalled"] for r in readings] == [False] * len(readings)
    worst = min(r["similarity"] for r in readings if r["ready"])
    measured = calibration["stall"]["measured"]
    assert worst == pytest.approx(measured["playing_worst_similarity"], abs=0.005)
    assert worst < float(calibration["stall"]["min_similarity"]) - 0.01, (
        f"the worst played window ({worst:.4f}) is too close to the threshold "
        f"({calibration['stall']['min_similarity']}) to trust"
    )


def test_the_first_threshold_anyone_proposes_would_fire_mid_game(stall, calibration):
    """Why 0.995 and not 0.95: at 0.95 this same played session reads as stalled.

    Kept as a test so nobody can "simplify" the threshold back to a round number
    without seeing what it does to a real session.
    """
    manifest = play_manifest()
    names = [frame["file"] for frame in manifest["frames"]]
    times = [frame["t_mono"] for frame in manifest["frames"]]
    readings = drive(stall, names, times, calibration, min_similarity=0.95)

    fired = [r for r in readings if r["stalled"]]
    assert fired, "0.95 no longer fires on this session; re-measure before trusting 0.995"
    assert all(r["similarity"] < 0.995 for r in readings if r["ready"])


def test_the_corpus_is_the_session_it_claims_to_be(stall, calibration):
    """The committed samples and the calibration's measured block must describe one thing."""
    manifest = play_manifest()
    measured = calibration["stall"]["measured"]
    assert "20261012T061819Z-passive" in manifest["source_session"]
    assert len(manifest["frames"]) == int(measured["corpus_frames"])
    span = manifest["frames"][-1]["t_mono"] - manifest["frames"][0]["t_mono"]
    assert span == pytest.approx(measured["corpus_span_s"], abs=0.1)
    assert [f["file"] for f in manifest["frames"]] == sorted(f["file"] for f in manifest["frames"])


# --------------------------------------------------------------------------- #
# The other side of the margin: a board that really has stopped
# --------------------------------------------------------------------------- #


def test_a_board_that_has_not_changed_for_the_window_reads_as_stalled(stall, calibration):
    """The panel on screen: the same pixels, five seconds apart."""
    frame = playfield("panel-gameover.png", directory=SAMPLES)
    tracker = stall.StallTracker(
        **stall.settings({**calibration, "board_roi_ratios": [0, 0, 1, 1]})
    )
    reading = tracker.push(0.0, stall.board_roi(frame, {"board_roi_ratios": [0, 0, 1, 1]}))
    assert reading["stalled"] is False, "a single frame is not five seconds of stillness"
    reading = tracker.push(5.2, stall.board_roi(frame, {"board_roi_ratios": [0, 0, 1, 1]}))
    assert reading["stalled"] is True
    assert reading["static_s"] == pytest.approx(5.2, abs=0.01)
    assert reading["similarity"] == pytest.approx(1.0, abs=1e-4)


def test_a_dimmed_board_is_still_the_same_board(stall):
    """On the device a modal scrim dimmed the whole screen to 0.61x; that is not a change.

    Un-normalised, every pixel of that frame differs by more than the tolerance, so the
    tracker would call a perfectly static board "changing" — and never end a round.
    """
    frame = playfield("panel-now.png", directory=SAMPLES)
    dimmed = frame * 0.61
    settings = {"board_roi_ratios": [0, 0, 1, 1]}
    tracker = stall.StallTracker(**stall.settings(settings))
    tracker.push(0.0, stall.board_roi(frame, settings))
    reading = tracker.push(5.1, stall.board_roi(dimmed, settings))
    assert reading["similarity"] == pytest.approx(1.0, abs=1e-3)
    assert reading["stalled"] is True


def test_one_piece_moving_is_not_a_stall(stall, calibration):
    """The sensitivity a 95 % threshold lacks: a single moved piece must break the stall.

    The piece is painted where the terrain is empty, so this is exactly "one tetromino
    moved" — the smallest change a playing board ever shows.
    """
    settings = {"board_roi_ratios": [0, 0, 1, 1]}
    tracker = stall.StallTracker(**stall.settings(settings))
    first = playfield("frame-0005.jpg")
    moved = first.copy()
    moved[300:430, 60:180] = 40.0  # a 3x3-cell block(ish) of a tetromino elsewhere
    tracker.push(0.0, stall.board_roi(first, settings))
    reading = tracker.push(5.1, stall.board_roi(moved, settings))
    assert reading["similarity"] < float(calibration["stall"]["min_similarity"])
    assert reading["stalled"] is False


def test_a_window_that_has_not_elapsed_yet_is_not_a_stall(stall):
    """Two frames a second apart say nothing about five seconds."""
    settings = {"board_roi_ratios": [0, 0, 1, 1]}
    tracker = stall.StallTracker(**stall.settings(settings))
    frame = playfield("panel-gameover.png", directory=SAMPLES)
    tracker.push(0.0, stall.board_roi(frame, settings))
    reading = tracker.push(1.0, stall.board_roi(frame, settings))
    assert reading["ready"] is False
    assert reading["static_s"] == pytest.approx(1.0, abs=0.01)
    assert reading["stalled"] is False


def test_the_settings_come_from_the_calibration(stall, calibration):
    settings = stall.settings(calibration)
    block = calibration["stall"]
    assert settings["window_s"] == block["window_s"]
    assert settings["min_similarity"] == block["min_similarity"]
    assert settings["tolerance"] == block["tolerance"]
    assert settings["downsample"] == block["downsample"]
    # A skill calibrated before this existed must not crash: defaults, and no readings.
    assert stall.settings({})["window_s"] > 0
    assert stall.board_roi(np.zeros((1600, 720, 3), dtype="float32"), {}) is None


def test_the_history_stays_bounded(stall, calibration):
    """A long session must not accumulate every frame it ever saw."""
    settings = {**calibration, "board_roi_ratios": [0, 0, 1, 1]}
    tracker = stall.StallTracker(**stall.settings(settings))
    frame = stall.board_roi(playfield("panel-gameover.png", directory=SAMPLES), settings)
    for step in range(200):
        reading = tracker.push(step * 0.7, frame)
    assert reading["stalled"] is True
    assert reading["samples"] <= int(settings["stall"]["max_samples"])
    assert tracker.resident_samples() <= int(settings["stall"]["max_samples"])


# --------------------------------------------------------------------------- #
# The inner loop: the reading reaches the state, and the session ends
# --------------------------------------------------------------------------- #


def test_perception_reports_its_stall_reading(perception, stall, calibration, monkeypatch):
    """A played frame carries a reading; a static screen ends up stalled."""
    clock = {"t": 0.0}
    monkeypatch.setattr(stall, "now", lambda: clock["t"])
    perception.reset_history()

    manifest = play_manifest()
    state = None
    for frame in manifest["frames"]:
        clock["t"] = frame["t_mono"]
        state = perception.parse(
            (PLAY / frame["file"]).read_bytes(),
            {**calibration, "board_roi_ratios": [0.0, 0.0, 1.0, 1.0]},
        )
    assert state is not None
    assert state["stall"]["stalled"] is False
    assert state["stall"]["ready"] is True

    panel = (SAMPLES / "panel-gameover.png").read_bytes()
    for _ in range(4):
        clock["t"] += 2.0
        state = perception.parse(panel, {**calibration, "board_roi_ratios": [0.0, 0.0, 1.0, 1.0]})
    assert state is not None
    assert state["stall"]["stalled"] is True
    assert state["game_over"] is True  # the panel check fires too, and they agree


def test_reset_clears_the_stall_history(perception, stall, calibration, monkeypatch):
    """A new session must not inherit the last one's stillness (the on_start hook)."""
    clock = {"t": 0.0}
    monkeypatch.setattr(stall, "now", lambda: clock["t"])
    settings = {**calibration, "board_roi_ratios": [0.0, 0.0, 1.0, 1.0]}
    panel = (SAMPLES / "panel-gameover.png").read_bytes()
    perception.reset_history()
    clock["t"] = 100.0
    perception.parse(panel, settings)
    perception.reset_history()
    clock["t"] = 106.0
    state = perception.parse(panel, settings)
    assert state["stall"]["stalled"] is False
    assert state["stall"]["samples"] == 1


@pytest.mark.parametrize("backend", ["jev", "search"])
def test_a_stalled_board_ends_the_session(calibration, backend):
    """Both backends end on the same measurement; the note carries what was measured."""
    dispatch = importlib.import_module("artemis_skills_tetris.policy_dispatch")
    state = {
        "board_matrix": ["0" * 10] * 20,
        "board": [
            [
                ".",
            ]
            * 10
        ]
        * 20,
        "current_piece_mask": [],
        "stall": {"stalled": True, "static_s": 5.6, "similarity": 0.9991, "window_s": 5.0},
        "counters": {},
    }
    action = dispatch.decide(state, {**calibration, "policy_backend": backend})
    assert action.done is True
    assert action.state["note"] == "board static"
    assert action.state["stall"]["static_s"] == pytest.approx(5.6)
    assert action.gestures == ()


def test_a_reading_below_the_window_does_not_end_anything(calibration):
    dispatch = importlib.import_module("artemis_skills_tetris.policy_dispatch")
    state = {
        "board_matrix": ["0" * 10] * 20,
        "board": [
            [
                ".",
            ]
            * 10
        ]
        * 20,
        "current_piece_mask": [],
        "stall": {"stalled": False, "static_s": 2.0, "similarity": 0.97, "window_s": 5.0},
        "counters": {},
        "current": None,
    }
    action = dispatch.decide(state, {**calibration, "policy_backend": "search"})
    assert action.done is False
