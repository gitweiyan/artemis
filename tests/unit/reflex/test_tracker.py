"""The falling piece, tracked once instead of re-derived every frame.

The frame-difference path (:mod:`pieces`) answers "what is moving?" independently on
every pair of frames. That is correct but fragile: it has to recognise the piece again
from scratch each time, so a frame where the shape reads a little differently, where the
piece has just spawned, or where it is already touching the stack comes back as "no
piece" — and the policy loses a frame it needed (on the device a piece is visible for
only one or two frames at all; see ``calibration.json`` → ``mechanics.fall_speed``).

This is the tracker from the handover design (``docs/superpowers/plans/
2026-10-13-jev-decision-redesign.md`` §11.2): **acquire** a piece once from the frame
difference, **track** it for the rest of its life by following the cells that are not
part of the terrain, and **release** it when it locks. The identity is what the policy
needs: one piece, one id, from the frame it appears until it lands.

The tracker keeps the terrain it measured at acquisition. A fall then shows up as
"cells filled now that were not in that terrain", which is true even while the piece is
touching the stack — the case the frame difference cannot separate. Tracking by cells
rather than by a stored bitmap is deliberate: the piece rotates and, at spawn, is
revealed row by row, so its bitmap changes while the piece is the same piece.

Validation runs offline on the committed corpus (``samples/session-play``, 43 real
frames of a game being played) and on a recorded trace, so the claim "more frames than
the frame difference" is measured, not asserted from a mock.
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
PLAY = TETRIS / "samples" / "session-play"
ROWS, COLUMNS = 20, 10
#: The committed samples are the playfield itself, so the whole image is the ROI.
WHOLE = {"board_roi_ratios": [0.0, 0.0, 1.0, 1.0]}


@pytest.fixture(scope="module")
def tracker_module():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.tracker")


@pytest.fixture(scope="module")
def pieces_module():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.pieces")


@pytest.fixture(scope="module")
def perception_module():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.perception")


@pytest.fixture(scope="module")
def calibration():
    return json.loads((TETRIS / "calibration.json").read_text(encoding="utf-8"))


def matrix(filled=(), *, rows=ROWS, columns=COLUMNS):
    """A raw board in the shape ``read_matrix`` returns: rows of "0"/"1"."""
    out = ["0" * columns for _ in range(rows)]
    for row, column in filled:
        out[row] = out[row][:column] + "1" + out[row][column + 1 :]
    return out


#: A settled stack with its top at row 7: a piece can land on it inside two frames.
STACK = [(6, 7), (6, 8), (6, 9), (7, 7), (7, 8), (7, 9)]
#: The first two rows the game reveals of a new piece, then the full piece one row lower.
SPAWN_TOP = [(0, 4), (0, 5)]
PIECE_1 = [(1, 4), (1, 5), (2, 5), (2, 6)]
PIECE_2 = [(3, 4), (3, 5), (4, 5), (4, 6)]
#: The same piece resting on the stack: the frame the frame difference gives up on.
PIECE_LANDED = [(4, 6), (4, 7), (5, 7), (5, 8)]


def read_corpus_matrices(perception, calibration) -> list[list[str]]:
    """The 43 real playfields as raw matrices, through the same pipeline ``parse`` uses.

    The samples are already the playfield crop, so the ROI is the whole image and the
    measured grid comes back 10x20 (the same identity check ``parse`` applies).
    """
    from artemis_skills_tetris import cells as cells_module
    from artemis_skills_tetris import grid_probe, roi

    settings = {**calibration, **WHOLE}
    manifest = json.loads((PLAY / "manifest.json").read_text(encoding="utf-8"))
    matrices = []
    for entry in manifest["frames"]:
        image = np.asarray(Image.open(PLAY / entry["file"]).convert("RGB"), dtype="float32")
        field = roi.board_crop(image, settings)
        probe = grid_probe.measure_grid(field, settings)
        measured = {
            **settings,
            "grid": {**settings["grid"], "cols": probe["columns"], "rows": probe["rows"]},
        }
        matrices.append(cells_module.read_matrix(field, measured))
    return matrices


def frame_difference_frames(matrices, pieces) -> int:
    """How many frames the existing frame-difference path reports a piece on."""
    history: list[list[str]] = []
    count = 0
    for current in matrices:
        history.append(current)
        result = pieces.split_pieces(history)
        if result["current_piece_positions"]:
            count += 1
    return count


def raw_from_trace(entry) -> list[str]:
    """A trace entry back to the raw board: the settled matrix plus the piece it removed."""
    board = [list(row) for row in entry["matrix"]]
    for row, column in entry.get("piece_positions") or []:
        board[row][column] = "1"
    return ["".join(row) for row in board]


def trace_matrices(stamp: str) -> list[list[str]]:
    path = Path("captures") / "play" / stamp / "trace.jsonl"
    if not path.exists():
        pytest.skip(f"no committed trace at {path}")
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [raw_from_trace(entry) for entry in entries if entry.get("kind") == "board"]


# --------------------------------------------------------------------------- #
# One identity for the whole fall
# --------------------------------------------------------------------------- #


def test_a_piece_keeps_one_identity_from_spawn_to_lock(tracker_module, calibration):
    """The spawn frame, the shape reveal, the fall and the landing are one piece.

    Spawn reveals the piece over two frames (measured on the device), and the frame
    before a lock has it touching the stack — the frame difference calls both "no
    piece". The tracker calls them the same piece, which is the whole point.
    """
    tracker = tracker_module.PieceTracker(calibration)
    sequence = [
        matrix(STACK),
        matrix(STACK + SPAWN_TOP),
        matrix(STACK + PIECE_1),
        matrix(STACK + PIECE_2),
        matrix(STACK + PIECE_LANDED),
        matrix(STACK + PIECE_LANDED),
    ]

    ids = [tracker.push(current)["piece_id"] for current in sequence]

    assert ids[0] is None, "nothing has appeared yet"
    assert ids[1] == ids[2] == ids[3] == ids[4], f"the piece changed identity mid-fall: {ids}"
    assert ids[5] is None, "the piece locked and must be released"
    assert ids[1] is not None


def test_the_piece_is_reported_over_more_frames_than_the_frame_difference(
    tracker_module, pieces_module, calibration
):
    """The measured claim: tracking covers frames the frame difference cannot."""
    sequence = [
        matrix(STACK),
        matrix(STACK + SPAWN_TOP),
        matrix(STACK + PIECE_1),
        matrix(STACK + PIECE_2),
        matrix(STACK + PIECE_LANDED),
        matrix(STACK + PIECE_LANDED),
    ]
    tracker = tracker_module.PieceTracker(calibration)
    tracked = sum(1 for current in sequence if tracker.push(current)["current_piece_positions"])
    diffed = frame_difference_frames(sequence, pieces_module)

    assert tracked > diffed, (
        f"the tracker ({tracked}) did not beat the frame difference ({diffed}) on the "
        "spawn/landing frames it exists for"
    )


def test_the_board_handed_over_is_the_stack_without_the_piece(tracker_module, calibration):
    """The policy needs the terrain, not the terrain plus the piece."""
    tracker = tracker_module.PieceTracker(calibration)
    tracker.push(matrix(STACK))
    state = tracker.push(matrix(STACK + PIECE_1))

    occupied = {
        (row, column)
        for row, line in enumerate(state["board_matrix"])
        for column, cell in enumerate(line)
        if cell == "1"
    }
    assert occupied == set(STACK)
    assert sorted(map(tuple, state["current_piece_positions"])) == sorted(PIECE_1)
    assert state["current_piece_mask"] == [[1, 1, 0], [0, 1, 1]]


#: The same piece in two orientations, as the frames either side of a rotate tap show it.
TURNED_A = [(4, 3), (5, 3), (5, 4), (5, 5)]
TURNED_B = [(4, 4), (4, 5), (5, 4), (6, 4)]


#: The same physical piece as a rotate tap plus a fast fall leaves it: two cells at the
#: top, then three cells four rows lower (measured, captures/play/20261013T091013Z f49/f50).
FAST_A = [(0, 4), (1, 4)]
FAST_B = [(4, 3), (4, 4), (4, 5)]


def test_a_piece_that_falls_fast_after_a_turn_is_still_the_same_piece(tracker_module, calibration):
    """A fast fall between captures must not release the piece.

    Device 2026-10-13 (captures/play/20261013T091013Z): 3 of 6 rotate decisions lost the
    piece before its move frame - it fell past the 2-row local window, the frame difference
    refused the rotated (reshaped) component, the tracker released, and the piece landed at
    its spawn column with no shift. That is the "no strategy in the second half" the
    operator saw. Replaying that trace with an 8-row window: 0 lost, 51 -> 58 tracked frames.
    """
    tracker = tracker_module.PieceTracker(calibration)
    tracker.push(matrix(STACK))
    first = tracker.push(matrix(STACK + FAST_A))
    state = tracker.push(matrix(STACK + FAST_B))

    assert first["piece_id"] is not None
    assert state["piece_id"] == first["piece_id"], (
        "a fast fall (or a turn) between captures released the piece; its move was then lost"
    )


def test_the_shape_is_reported_as_settled_only_after_it_stops_changing(tracker_module, calibration):
    """``piece_settled`` is True only once the bitmap has held still for a frame.

    The policy waits on it so a plan is made for the shape that is really on screen (device
    2026-10-13 run14: a 2-cell domino was planned while the piece was still revealing into a
    3-cell bar, and the rotate guard ended the session).
    """
    tracker = tracker_module.PieceTracker(calibration)
    tracker.push(matrix(STACK))

    assert tracker.push(matrix(STACK + SPAWN_TOP))["piece_settled"] is False, "just acquired"
    assert tracker.push(matrix(STACK + PIECE_1))["piece_settled"] is False, "the bitmap grew"
    # The same bitmap, one row lower: the shape has settled (the piece is only moving).
    settled = tracker.push(matrix(STACK + PIECE_2))
    assert settled["current_piece_positions"]
    assert settled["piece_settled"] is True, "the bitmap held still"


def test_a_turn_while_tracked_does_not_report_sideways_motion(tracker_module, calibration):
    """The tracker must not hand the policy a gravity signal for a rotation.

    Device evidence (2026-10-13): an acquired, rotated piece reported ``motion_vector ==
    [0, 1]``; the policy read gravity "right", built candidates up to column 16 on a
    10-column board, and tapped right 11 times (``captures/play/20261013T080118Z``).
    """
    """The tracker must not hand the policy a gravity signal for a rotation.

    Device evidence (2026-10-13): an acquired, rotated piece reported ``motion_vector ==
    [0, 1]``; the policy read gravity "right", built candidates up to column 16 on a
    10-column board, and tapped right 11 times (``captures/play/20261013T080118Z``).
    """
    tracker = tracker_module.PieceTracker(calibration)
    tracker.push(matrix(STACK))
    tracker.push(matrix(STACK + TURNED_A))

    state = tracker.push(matrix(STACK + TURNED_B))

    assert state["piece_id"] is not None, "a turn does not end the lifecycle"
    assert state["current_piece_positions"]
    assert state["motion_vector"] is None, (
        "the bitmap changed, so the centroid move is a rotation, not a direction of fall"
    )


def test_a_new_piece_gets_a_new_identity(tracker_module, calibration):
    """After a lock, the next piece must not inherit the last piece's id."""
    tracker = tracker_module.PieceTracker(calibration)
    tracker.push(matrix(STACK))
    first = tracker.push(matrix(STACK + PIECE_1))["piece_id"]
    tracker.push(matrix(STACK + PIECE_2))
    tracker.push(matrix(STACK + PIECE_LANDED))
    tracker.push(matrix(STACK + PIECE_LANDED))  # release

    second = tracker.push(matrix(STACK + SPAWN_TOP))["piece_id"]

    assert first != second
    assert second is not None


def test_a_locked_piece_does_not_swallow_the_next_one(tracker_module, calibration):
    """The failure this tracker must not have: the identity sticking to the stack.

    A locked piece is a contiguous run like any other; tracking it as the falling
    piece forever would hand the policy a piece that cannot be moved. The release is
    what lets the next spawn be seen as a new piece instead of the same one.
    """
    tracker = tracker_module.PieceTracker(calibration)
    tracker.push(matrix(STACK))
    tracker.push(matrix(STACK + PIECE_1))
    tracker.push(matrix(STACK + PIECE_2))
    tracker.push(matrix(STACK + PIECE_LANDED))
    released = tracker.push(matrix(STACK + PIECE_LANDED))
    assert released["piece_id"] is None

    acquired = tracker.push(matrix(STACK + PIECE_LANDED + [(0, 4), (0, 5)]))
    assert acquired["piece_id"] is not None
    assert acquired["current_piece_positions"] == [[0, 4], [0, 5]]


def test_reset_forgets_the_piece_in_flight(tracker_module, calibration):
    """A new session must not inherit the last one's piece (the ``on_start`` hook)."""
    tracker = tracker_module.PieceTracker(calibration)
    tracker.push(matrix(STACK))
    first = tracker.push(matrix(STACK + PIECE_1))
    assert first["piece_id"] == 1

    tracker.reset()
    assert tracker.push(matrix(STACK + PIECE_2))["piece_id"] is None, (
        "after reset the first frame has no previous frame to acquire from"
    )
    second = tracker.push(matrix(STACK + PIECE_1))
    assert second["piece_id"] is not None


# --------------------------------------------------------------------------- #
# The offline corpus: measure it on real frames, not a mock
# --------------------------------------------------------------------------- #


def test_the_tracker_beats_the_frame_difference_on_the_real_corpus(
    tracker_module, pieces_module, perception_module, calibration
):
    """43 real frames of a game being played: the tracker reports more piece-frames."""
    matrices = read_corpus_matrices(perception_module, calibration)
    tracker = tracker_module.PieceTracker(calibration)
    tracked = sum(1 for current in matrices if tracker.push(current)["current_piece_positions"])
    diffed = frame_difference_frames(matrices, pieces_module)

    assert diffed > 0, "the corpus should contain moving pieces"
    # Measured 2026-10-13: tracker 38/43 frames (88%) against the frame difference 28/43
    # (65%), with four lifecycles released as "locked" (10, 11, 9 and 8 frames).
    assert tracked >= diffed + 5, (
        f"tracker {tracked}/{len(matrices)} frames vs frame difference {diffed}: "
        "the corpus is the regression that keeps the tracker better than re-deriving"
    )
    assert tracked >= len(matrices) * 0.8, (
        f"the tracker reported only {tracked}/{len(matrices)} frames; a piece should be "
        "tracked through its whole fall, not just the frames it is trivially moving"
    )


def test_each_identity_is_one_contiguous_lifecycle(tracker_module, perception_module, calibration):
    """Ids never come back: a piece is born, tracked, and released once."""
    matrices = read_corpus_matrices(perception_module, calibration)
    tracker = tracker_module.PieceTracker(calibration)
    seen: list[int] = []
    for current in matrices:
        piece_id = tracker.push(current)["piece_id"]
        if piece_id is not None:
            seen.append(piece_id)

    # Contiguous runs, and each id appears in exactly one run.
    runs: list[int] = []
    for piece_id in seen:
        if not runs or runs[-1] != piece_id:
            runs.append(piece_id)
    assert len(runs) == len(set(runs)), f"an id was reused after a gap: {runs}"


def test_a_recorded_trace_releases_every_lifecycle(tracker_module, calibration):
    """A whole real run: pieces come and go, and nothing is left tracked at the end."""
    matrices = trace_matrices("20261012T141809Z")
    tracker = tracker_module.PieceTracker(calibration)
    ids = [tracker.push(current)["piece_id"] for current in matrices]

    assert sum(1 for piece_id in ids if piece_id is not None) > 0
    # The trace can end mid-piece; one more identical frame is what a lock looks like, and
    # that frame must release the identity rather than hold it forever.
    ids.append(tracker.push(matrices[-1])["piece_id"])
    assert ids[-1] is None, "the tracker ended still holding a piece"

    # No identity is longer than a piece could plausibly live: at ~2 fps and ~2 rows/s a
    # piece crosses the board in a handful of frames; a run of dozens would be a stack
    # that got stuck to an identity.
    longest, current, previous = 0, 0, None
    for piece_id in ids:
        if piece_id is not None and piece_id == previous:
            current += 1
        else:
            current = 1 if piece_id is not None else 0
        previous = piece_id
        longest = max(longest, current)
    assert longest <= 20, f"an identity lasted {longest} frames — it stuck to the stack"
