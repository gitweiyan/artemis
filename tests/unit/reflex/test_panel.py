"""The game-over panel: read off the screen, never assumed.

The operator's rule for this game: the session ends when the page shows "游戏结束" and a
new one starts by tapping the "重新开始" button. Neither string is in the accessibility
tree (this is a Unity surface), so both have to come from the pixels — and the repo's OCR
is a cloud service, which the inner loop must not call.

**What is not claimed here.** Nothing in this file reads the glyphs. *Which* characters
sit in the title box is the operator's reading of a live screenshot; a test cannot
establish that and none pretends to. What the assertions below do establish is the part a
reader should not have to take on trust:

* the title box is a *panel-specific* patch — it scores 1.000 against a second capture of
  the panel and at most 0.051 against six frames of the game being played, so one
  threshold separates them with a wide margin (the numbers are in ``calibration.json``'s
  ``panel.measured`` and are re-checked against the frames here);
* the detector answers to the ROI's *content*, not to the frame's identity: pasting the
  template onto a gameplay frame makes it fire, blanking the panel's own ROI makes it
  stop;
* the restart point sits inside the wide white glyph group the frame actually shows at
  that spot, so the tap lands on a label rather than on the panel's background.

The corpus holds two captures of the panel on purpose: they differ off the ROI (one has
the ad banner on the lower screen, one does not), so the verdict can be shown not to
depend on the rest of the screen.

The restart flow is device-free by construction: ``panel.restart(look, tap, ...)`` takes
the screen reader and the tapper as arguments, so how many taps the button needs is
decided by what the screen shows afterwards — the plan's open question ("tap once, wait
5 s, tap again?") is answered at run time instead of guessed here.
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

#: Frames of the game being played — the counter-examples the detector must ignore.
GAMEPLAY_FRAMES = (
    "session-0001.png",
    "session-0002.png",
    "session-0021.png",
    "reference-1.jpg",
    "reference-2.jpg",
    "reference-3.jpg",
)
#: Two captures of the game-over panel; they differ below the ROI (the ad banner).
PANEL_FRAMES = ("panel-gameover.png", "panel-now.png")


@pytest.fixture(scope="module")
def panel():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.panel")


@pytest.fixture(scope="module")
def perception():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.perception")


@pytest.fixture(scope="module")
def calibration():
    return json.loads((TETRIS / "calibration.json").read_text(encoding="utf-8"))


def frame(name: str) -> np.ndarray:
    return np.asarray(Image.open(SAMPLES / name).convert("RGB"), dtype="float32")


def box_of(ratios, shape) -> tuple[int, int, int, int]:
    """``(x0, y0, x1, y1)`` in pixels — the order ``roi.RATIO_KEY`` uses."""
    height, width = shape[:2]
    x0, y0, x1, y1 = (float(value) for value in ratios)
    return (
        round(x0 * width),
        round(y0 * height),
        round(x1 * width),
        round(y1 * height),
    )


class Screen:
    """Stands in for the device: a fixed list of frames, then the last one forever."""

    def __init__(self, frames: list[bytes]):
        self.frames = frames
        self.reads = 0

    def look(self):
        self.reads += 1
        return self.frames[min(self.reads - 1, len(self.frames) - 1)]


def bytes_of(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


# --------------------------------------------------------------------------- #
# The title box: panel-specific, and answered by its content
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", PANEL_FRAMES)
def test_the_game_over_panel_is_recognised(panel, calibration, name):
    score = panel.score(frame(name), calibration)
    assert panel.is_game_over(frame(name), calibration) is True
    assert score >= 0.9, f"{name}: expected a near-exact match, scored {score:.3f}"


@pytest.mark.parametrize("name", GAMEPLAY_FRAMES)
def test_no_frame_of_the_game_being_played_is_read_as_the_panel(panel, calibration, name):
    score = panel.score(frame(name), calibration)
    threshold = float(calibration["panel"]["match_threshold"])
    assert panel.is_game_over(frame(name), calibration) is False
    # Half the threshold, not merely under it: the margin is the point of this test.
    assert score < threshold / 2, f"{name}: scored {score:.3f} against threshold {threshold}"


def test_the_scores_recorded_in_the_calibration_are_what_these_frames_produce(panel, calibration):
    """``panel.measured`` is a claim about this corpus; a claim nobody re-checks rots."""
    measured = calibration["panel"]["measured"]
    panels = [panel.score(frame(name), calibration) for name in PANEL_FRAMES]
    playing = [panel.score(frame(name), calibration) for name in GAMEPLAY_FRAMES]
    assert len(panels) == int(measured["panel_frames_checked"])
    assert len(playing) == int(measured["playing_frames_checked"])
    assert min(panels) == pytest.approx(float(measured["panel_score"]), abs=0.001)
    assert max(playing) == pytest.approx(float(measured["max_playing_score"]), abs=0.01)


def test_the_detector_answers_to_the_roi_not_to_the_frame(panel, calibration):
    """Paste the template onto a gameplay frame: it must fire. Blank the panel's ROI: not.

    Without this, a detector that had somehow memorised "this screenshot is the panel"
    would still pass every test above.
    """
    template = panel.template(calibration)
    assert template is not None
    x0, y0, x1, y1 = box_of(calibration["panel"]["roi_ratios"], (1600, 720))

    pasted = frame(GAMEPLAY_FRAMES[0]).copy()
    pasted[y0:y1, x0:x1, :] = template[:, :, None]
    assert panel.is_game_over(pasted, calibration) is True

    blanked = frame(PANEL_FRAMES[0]).copy()
    blanked[y0:y1, x0:x1, :] = 0.0
    assert panel.is_game_over(blanked, calibration) is False


def test_the_ad_on_the_lower_screen_changes_nothing(panel, calibration):
    """The two panel captures differ off the ROI; the verdict must be the same."""
    with_ad, ad_free = (frame(name) for name in PANEL_FRAMES)
    off_roi = np.abs(with_ad[1245:, :, :] - ad_free[1245:, :, :]).max()
    assert off_roi > 50, "the corpus no longer holds one ad frame and one ad-free frame"
    assert panel.is_game_over(with_ad, calibration) is panel.is_game_over(ad_free, calibration)


def test_a_screen_with_nothing_on_it_is_not_a_panel(panel, calibration):
    black = np.zeros((1600, 720, 3), dtype="float32")
    noise = np.random.default_rng(0).integers(0, 255, (1600, 720, 3)).astype("float32")
    assert panel.is_game_over(black, calibration) is False
    assert panel.is_game_over(noise, calibration) is False


def test_the_template_is_the_box_the_calibration_declares(panel, calibration):
    """The committed template and the declared ROI must not drift apart.

    They are two files describing one rectangle: a template cropped from one capture and
    ratios read off another would silently compare the wrong pixels.
    """
    x0, y0, x1, y1 = box_of(calibration["panel"]["roi_ratios"], (1600, 720))
    assert [x0, y0, x1, y1] == list(calibration["panel"]["measured"]["template_box_px"])
    crop = frame(PANEL_FRAMES[0])[y0:y1, x0:x1].mean(axis=2)
    assert np.abs(crop - panel.template(calibration)).max() == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# The state the runtime sees
# --------------------------------------------------------------------------- #


def test_parse_reports_the_panel_as_game_over_instead_of_blind(perception, calibration):
    """A panel frame must not be "not a board": that ends the session as a blind stop.

    The measured pipeline rejects it earlier (its identity check sees no 10x20 board), so
    without an explicit panel check the session would end on blind frames, with no reason
    recorded — and a restart flow would not know why it ended.
    """
    state = perception.parse(type("F", (), {"image": bytes_of(PANEL_FRAMES[0])})(), calibration)
    assert state is not None, "the panel was read as a non-board frame"
    assert state["game_over"] is True
    assert not state.get("board_matrix")


def test_parse_still_reports_a_board_frame_as_not_game_over(perception, calibration):
    state = perception.parse(type("F", (), {"image": bytes_of("session-0001.png")})(), calibration)
    assert state is not None
    assert state["game_over"] is False


@pytest.mark.parametrize(
    ("name", "game_over"),
    [(PANEL_FRAMES[0], True), ("session-0001.png", False)],
)
def test_a_frame_may_be_raw_bytes_as_well_as_a_capture(perception, calibration, name, game_over):
    """``panel`` takes bytes or a capture; ``perception.parse`` must not disagree.

    The asymmetry is a trap rather than a style point: a caller that hands ``panel`` raw
    bytes and ``perception`` the same bytes would get "the panel" from one and "no board"
    from the other, and the restart flow would report a readable board as missing.
    """
    raw = bytes_of(name)
    capture = type("F", (), {"image": raw})()
    from_bytes = perception.parse(raw, calibration)
    from_capture = perception.parse(capture, calibration)
    assert from_bytes is not None and from_capture is not None
    assert from_bytes["game_over"] is game_over is from_capture["game_over"]
    assert bool(from_bytes.get("board_matrix")) == bool(from_capture.get("board_matrix"))


def test_the_game_over_state_ends_the_session_on_the_jev_backend(calibration):
    """The end-of-session rule must survive the backend switch to ``jev``."""
    dispatch = importlib.import_module("artemis_skills_tetris.policy_dispatch")
    state = {"game_over": True, "counters": {}, "board_matrix": None, "board": []}
    action = dispatch.decide(state, {**calibration, "policy_backend": "jev"})
    assert action.done is True
    assert action.state.get("note") == "game over"


# --------------------------------------------------------------------------- #
# The restart button: where it is, and what it takes to press it
# --------------------------------------------------------------------------- #


def test_the_restart_point_is_where_the_operator_sees_the_button(panel, calibration):
    assert panel.restart_point(calibration) == (475, 1024)


def test_the_restart_point_lands_on_the_white_label_the_frame_shows(panel, calibration):
    """The tap must land on the button's label, not on the panel's background.

    The label is found in the frame rather than assumed: the widest run of white glyphs
    inside the declared label ROI. 92 px of white in one row, in a four-glyph group, is a
    label; the operator's point has to be inside it.
    """
    image = frame(PANEL_FRAMES[0])
    x0, y0, x1, y1 = box_of(calibration["restart_flow"]["label_roi_ratios"], image.shape)
    grey = image[y0:y1, x0:x1]
    white = (grey.min(axis=2) > 170) & ((grey.max(axis=2) - grey.min(axis=2)) < 45)

    columns = np.nonzero(white.any(axis=0))[0]
    assert columns.size, "no white glyphs inside the declared label ROI"
    runs: list[tuple[int, int]] = []
    start = previous = int(columns[0])
    for column in columns[1:]:
        column = int(column)
        if column - previous > 6:
            runs.append((start, previous))
            start = column
        previous = column
    runs.append((start, previous))

    point_x, point_y = panel.restart_point(calibration)
    inside = [run for run in runs if run[0] + x0 <= point_x <= run[1] + x0]
    assert inside, f"the restart point is on no glyph run: {[(a + x0, b + x0) for a, b in runs]}"
    left, right = inside[0]
    assert right - left + 1 >= 80, f"a {right - left + 1} px run is not a four-glyph label"
    rows = np.nonzero(white[:, left : right + 1].any(axis=1))[0]
    assert rows.min() + y0 <= point_y <= rows.max() + y0


def test_the_restart_taps_the_button_until_the_board_is_back(panel, calibration):
    """The plan's open question — one tap or two — is settled by looking, not by guessing."""
    screen = Screen(
        [bytes_of(PANEL_FRAMES[0]), bytes_of(PANEL_FRAMES[1]), bytes_of("session-0001.png")]
    )
    taps: list[tuple[int, int]] = []
    report = panel.restart(screen.look, taps.append, calibration, sleep=lambda _s: None)
    assert taps == [(475, 1024), (475, 1024)]
    assert report["taps"] == 2
    assert report["restarted"] is True


def test_the_restart_leaves_a_screen_that_is_not_the_panel_alone(panel, calibration):
    screen = Screen([bytes_of("session-0001.png")])
    taps: list[tuple[int, int]] = []
    report = panel.restart(screen.look, taps.append, calibration, sleep=lambda _s: None)
    assert taps == []
    assert report == {"taps": 0, "restarted": True, "reason": "no panel on screen"}


def test_the_restart_gives_up_and_says_so(panel, calibration):
    """A panel that never goes away must not be tapped forever."""
    screen = Screen([bytes_of(PANEL_FRAMES[0])])
    taps: list[tuple[int, int]] = []
    report = panel.restart(screen.look, taps.append, calibration, max_taps=3, sleep=lambda _s: None)
    assert len(taps) == 3
    assert report["restarted"] is False
    assert report["reason"] == "the panel is still on screen after 3 taps"


def test_an_uncalibrated_skill_recognises_nothing_and_taps_nothing(panel):
    empty = {"restart_flow": {}}
    assert panel.is_game_over(np.zeros((1600, 720, 3), dtype="float32"), {}) is False
    assert panel.restart_point(empty) is None
    taps: list[tuple[int, int]] = []
    report = panel.restart(lambda: bytes_of(PANEL_FRAMES[0]), taps.append, empty)
    assert taps == []
    assert report["restarted"] is False


@pytest.mark.parametrize(
    "panel_block",
    [
        {"template": "samples/no-such-file.png", "roi_ratios": [0.379167, 0.345, 0.619444, 0.395]},
        {"template": "calibration.json", "roi_ratios": [0.379167, 0.345, 0.619444, 0.395]},
        {"template": "samples/panel-title.png"},
        {"roi_ratios": [0.379167, 0.345, 0.619444, 0.395]},
    ],
)
def test_a_broken_panel_block_is_offline_not_fatal(panel, calibration, panel_block):
    """A missing file, a file that is not an image, or a missing box: no match, no crash.

    The inner loop must survive a calibration typo — an exception here would surface as a
    skill error mid-session rather than as "this screen is not the panel".
    """
    broken = {**calibration, "panel": panel_block}
    assert panel.is_game_over(frame(PANEL_FRAMES[0]), broken) is False
