"""The message Jev is asked about, assembled in one place.

Jev takes text and no images, so this dict is the whole interface between computer
vision and the decision: what is missing here is invisible to the model, and what is
extra here nobody agreed to read. Hence a fixed key set, built by one function.

Two of its properties are deliberate and were decided elsewhere:

* the piece travels as **geometry** — a mask plus its positions — never as a letter.
  A game with custom shapes has no letters to name them (Review Focus 4), and a letter
  would make the model's answer depend on a shape table it cannot see;
* ``mechanics.gravity_direction`` comes from the piece's **measured** displacement
  when there is one, and only falls back to the declared value otherwise. A gravity
  switch mid-session (Review Focus 3) changes the legal-move set, and an assumption
  would keep the model ranking moves that no longer exist.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

#: What a state without a declared gravity or piece source is assumed to be: the
#: standard rules and pieces read from the screen.
DEFAULT_GRAVITY = "down"
DEFAULT_PIECE_SOURCE = "vision"

#: Measured displacement → gravity direction.
_DIRECTIONS = {
    (1, 0): "down",
    (-1, 0): "up",
    (0, -1): "left",
    (0, 1): "right",
}


def gravity_direction(motion_vector: Sequence[int] | None, declared: str | None = None) -> str:
    """The direction the piece is actually moving in, else what was declared."""
    measured = _direction_from_motion(motion_vector)
    if measured:
        return measured
    return str(declared or DEFAULT_GRAVITY)


def _direction_from_motion(motion_vector: Sequence[int] | None) -> str | None:
    if not motion_vector or len(motion_vector) != 2:
        return None
    row, column = int(motion_vector[0]), int(motion_vector[1])
    if row == 0 and column == 0:
        return None
    if abs(row) >= abs(column):
        return _DIRECTIONS[(1 if row > 0 else -1, 0)]
    return _DIRECTIONS[(0, 1 if column > 0 else -1)]


def build_state(
    board_matrix: Iterable[str],
    pieces: dict[str, Any] | None,
    mechanics: dict[str, Any] | None,
    next_preview,
    candidates: Iterable[Any] | None,
) -> dict[str, Any]:
    """The state a Jev question is asked about — exactly these keys, nothing else.

    ``pieces`` is :func:`pieces.split_pieces`'s dict (a missing or empty one means the
    frame held no piece, and the message says so rather than inventing one).
    ``candidates`` are legal placements, or ``(placement, mask)`` pairs as
    :func:`simulator.legal_placements` returns them.
    """
    pieces = pieces or {}
    declared = mechanics or {}
    mask = _mask(pieces.get("current_piece_mask") or [], "current_piece_mask")
    motion = _motion(pieces.get("motion_vector"))
    return {
        "board_matrix": ["".join(row) for row in board_matrix],
        "current_piece_mask": mask,
        "current_piece_positions": [
            [int(row), int(column)] for row, column in pieces.get("current_piece_positions") or []
        ],
        "motion_vector": motion,
        "mechanics": {
            "gravity_direction": gravity_direction(motion, declared.get("gravity_direction")),
            "piece_source": str(declared.get("piece_source") or DEFAULT_PIECE_SOURCE),
        },
        "next_preview": _preview(next_preview),
        "candidate_moves": [_candidate(entry) for entry in candidates or []],
    }


def _candidate(entry: Any) -> dict[str, Any]:
    """One candidate move, with the id an answer has to name to be understood."""
    placement = entry[0] if isinstance(entry, (tuple, list)) else entry
    if isinstance(placement, dict):
        rotation, column = int(placement["rotation"]), int(placement["column"])
    else:
        rotation, column = int(placement.rotation), int(placement.column)
    return {"id": f"r{rotation}c{column}", "rotation": rotation, "column": column}


def _motion(motion_vector) -> list[int] | None:
    if not motion_vector or len(motion_vector) != 2:
        return None
    return [int(motion_vector[0]), int(motion_vector[1])]


def _mask(mask, name: str) -> list[list[int]]:
    if mask is None or (isinstance(mask, str) and not mask):
        return []
    if isinstance(mask, str):
        raise ValueError(
            f"{name} must be a geometric mask (rows of 0/1), not a letter:"
            " a custom shape set has no letters to name shapes with"
        )
    return [[int(cell) for cell in row] for row in mask]


def _preview(next_preview):
    """The next piece as geometry, or ``None`` when the screen does not show one."""
    if next_preview is None:
        return None
    return _mask(next_preview, "next_preview")


__all__ = ["DEFAULT_GRAVITY", "DEFAULT_PIECE_SOURCE", "build_state", "gravity_direction"]
