"""T5: which cells are the piece in play, and which way is it moving.

Colour cannot answer this. The falling piece and the stack it is about to land on use
the same seven colours — once a piece locks, its cells are indistinguishable from the
cells under it (Review Focus 2). Motion can: the piece is whatever changed between
consecutive frames, and the settled stack is everything else.

Two frames are enough, and that matters, because the device's inner loop is 2-3 fps
(330-490 ms per capture): a piece falls several cells between captures.

The motion vector carries the gravity direction — ``[1, 0]`` down, ``[-1, 0]`` up,
``[0, -1]`` left, ``[0, 1]`` right. That is what lets a policy notice that the game
switched gravity mid-session (Review Focus 3): the legal moves are a different set,
and a policy that did not notice would keep playing the old board.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from artemis.reflex.registry import load_skill

SKILLS = Path(__file__).resolve().parents[3] / "skills"
ROWS, COLUMNS = 20, 10


@pytest.fixture(scope="module")
def pieces():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.pieces")


def matrix(filled=(), *, rows=ROWS, columns=COLUMNS):
    """A matrix in the shape ``read_matrix`` returns: ``rows`` strings of ``columns``."""
    out = ["0" * columns for _ in range(rows)]
    for row, column in filled:
        out[row] = out[row][:column] + "1" + out[row][column + 1 :]
    return out


#: A settled stack on the floor, and the one cell above it.
STACK = [(19, 0), (19, 1), (19, 2), (18, 0)]
#: An L in play, and the same L one row lower: the same four cells, translated.
L_HIGH = [(15, 3), (15, 4), (15, 5), (16, 3)]
L_LOW = [(16, 3), (16, 4), (16, 5), (17, 3)]


def test_the_falling_piece_is_separated_from_the_stack(pieces):
    result = pieces.split_pieces([matrix(STACK + L_HIGH), matrix(STACK + L_LOW)])

    assert result["board_matrix"] == matrix(STACK), "the piece is not part of the terrain"
    assert sorted(result["current_piece_positions"]) == sorted([list(cell) for cell in L_LOW])
    assert result["motion_vector"] == [1, 0]


def test_the_piece_is_reported_as_geometry_not_as_a_letter(pieces):
    """Which tetromino it is does not matter: a custom shape set has no letters."""
    result = pieces.split_pieces([matrix(STACK + L_HIGH), matrix(STACK + L_LOW)])

    # rows 16-17, columns 3-5 of the piece's own bounding box
    assert result["current_piece_mask"] == [[1, 1, 1], [1, 0, 0]]


def test_a_board_with_no_falling_piece_reports_no_motion(pieces):
    """Nothing changed: what is on the board is the board."""
    result = pieces.split_pieces([matrix(STACK), matrix(STACK)])

    assert result["board_matrix"] == matrix(STACK)
    assert result["current_piece_positions"] == []
    assert result["current_piece_mask"] == []
    assert result["motion_vector"] is None


def test_the_motion_vector_reverses_with_the_gravity(pieces):
    """Review Focus 3: the same piece one row *up* is a different gravity, not noise."""
    falling = pieces.split_pieces([matrix(STACK + L_HIGH), matrix(STACK + L_LOW)])
    rising = pieces.split_pieces([matrix(STACK + L_LOW), matrix(STACK + L_HIGH)])

    assert falling["motion_vector"] == [1, 0]
    assert rising["motion_vector"] == [-1, 0]
    assert rising["board_matrix"] == matrix(STACK)


def test_a_sideways_move_is_reported_sideways(pieces):
    """Review Focus 3 again, the other axis: gravity can point left or right too."""
    result = pieces.split_pieces(
        [matrix(STACK + L_HIGH), matrix(STACK + [(15, 5), (15, 6), (15, 7), (16, 5)])]
    )

    assert result["motion_vector"] == [0, 2]


#: The same J shape in two orientations (measured on the device: this is what the two
#: frames either side of a rotate tap look like). Its centroid moves one column and not a
#: row, so a centroid displacement calls it a sideways fall.
TURNED_A = [(4, 3), (5, 3), (5, 4), (5, 5)]
TURNED_B = [(4, 4), (4, 5), (5, 4), (6, 4)]


def test_a_turned_piece_is_not_reported_as_a_sideways_fall(pieces):
    """A rotation moves the centroid; that is not the direction gravity pulls.

    Device evidence (2026-10-13): this exact pair gave ``motion_vector == [0, 1]``, which
    the policy read as gravity "right", so it enumerated columns along the board's height
    and then tapped right **11 times** at a 10-column board (`captures/play/
    20261013T080118Z`). A displacement is only a gravity reading when the bitmap is
    unchanged - i.e. the piece translated rather than turned.
    """
    result = pieces.split_pieces([matrix(STACK + TURNED_A), matrix(STACK + TURNED_B)])

    assert result["current_piece_positions"], "the same shape, so it is still the piece"
    assert result["motion_vector"] is None, (
        "a turn moved the centroid sideways; reporting it as translation makes the policy "
        "believe gravity changed direction"
    )


def test_any_shape_is_read_from_its_own_geometry(pieces):
    """Review Focus 4: a 2x3 rectangle is not in the standard seven, and needs no letter."""
    block = [(14, 6), (14, 7), (15, 6), (15, 7), (16, 6), (16, 7)]
    lower = [(row + 1, column) for row, column in block]

    result = pieces.split_pieces([matrix(STACK + block), matrix(STACK + lower)])

    assert result["current_piece_mask"] == [[1, 1], [1, 1], [1, 1]]
    assert len(result["current_piece_positions"]) == 6
    assert result["motion_vector"] == [1, 0]


def test_one_frame_cannot_separate_a_piece_from_the_stack(pieces):
    """With a single frame there is no motion to separate on: no piece is claimed.

    The conservative answer, and the honest one: inventing a piece from one frame
    would hand the policy a board with a hole in it.
    """
    result = pieces.split_pieces([matrix(STACK + L_HIGH)])

    assert result["current_piece_positions"] == []
    assert result["motion_vector"] is None
    assert result["board_matrix"] == matrix(STACK + L_HIGH)


def test_a_board_that_only_grew_reports_no_piece(pieces):
    """Measured on the real reference frames: this is what they actually look like.

    Read one after the other, the three device frames show a board that gained cells
    and lost none — not one piece falling between captures. Without this guard the
    "piece" is the whole changed region (16 cells, four rows of the stack) and the
    terrain is left almost empty. A piece has a *before* as well as an after: if
    nothing vanished, nothing moved, and the honest answer is no piece.
    """
    grown = matrix(STACK + L_HIGH)

    result = pieces.split_pieces([matrix(STACK), grown])

    assert result["current_piece_positions"] == []
    assert result["current_piece_mask"] == []
    assert result["motion_vector"] is None
    assert result["board_matrix"] == grown, "an unexplained change is still the board"


# --------------------------------------------------------------------------- #
# When the separation cannot be trusted, claim nothing (fixed after review)
# --------------------------------------------------------------------------- #

#: A stack that fills the bottom-left corner; a bar lands directly on top of it.
STACK_ON_FLOOR = [
    (19, 0),
    (19, 1),
    (19, 2),
    (19, 3),
    (19, 4),
    (18, 0),
    (18, 1),
    (18, 2),
    (18, 3),
    (18, 4),
]
BAR_ABOVE = [(16, 1), (16, 2), (16, 3)]
BAR_ON_STACK = [(17, 1), (17, 2), (17, 3)]


def test_a_piece_that_touches_the_stack_is_not_claimed_at_all(pieces):
    """The frame before every lock: the piece is adjacent to the stack it lands on.

    Connectivity alone merges the two — measured: a 13-cell "piece" spanning the whole
    stack and a terrain of *zero* cells, which is a board that does not exist. A piece
    that translates keeps its shape; a region that grew by swallowing the stack does
    not, and saying "no piece" is the honest answer.
    """
    result = pieces.split_pieces(
        [matrix(STACK_ON_FLOOR + BAR_ABOVE), matrix(STACK_ON_FLOOR + BAR_ON_STACK)]
    )

    assert result["current_piece_positions"] == []
    assert result["current_piece_mask"] == []
    assert result["motion_vector"] is None
    assert result["board_matrix"] == matrix(STACK_ON_FLOOR + BAR_ON_STACK)
    assert sum(row.count("1") for row in result["board_matrix"]) == 13, (
        "the board is still the board"
    )


def test_a_piece_moving_while_it_touches_the_stack_is_not_claimed_either(pieces):
    """The merge in both frames: same size, different shape — still not a piece."""
    result = pieces.split_pieces(
        [
            matrix(STACK_ON_FLOOR + [(17, 1), (17, 2)]),
            matrix(STACK_ON_FLOOR + [(17, 2), (17, 3)]),
        ]
    )

    assert result["current_piece_positions"] == []
    assert result["current_piece_mask"] == []
    assert result["motion_vector"] is None


def test_frames_whose_grids_differ_are_not_a_motion_reading(pieces):
    """A changed grid measurement must not index off the end of the previous one.

    Measured on real frames from this device: a video-app screen whose texture measures
    a 67x133 grid, read after a real board, raised IndexError out of ``parse`` — which the
    runtime reports as a skill error instead of stopping as blind.
    """
    result = pieces.split_pieces([matrix(STACK), matrix([(5, 5)], rows=13, columns=7)])

    assert result["current_piece_positions"] == []
    assert result["motion_vector"] is None
    assert result["board_matrix"] == matrix([(5, 5)], rows=13, columns=7)
