"""The piece in play, separated from the stack it will land on — by motion, not colour.

A falling piece and the stack below it are drawn in the same seven colours, so
colour cannot tell them apart; once a piece locks, its cells look exactly like the
cells under it. What does tell them apart is change: between two consecutive frames
the piece's cells appear somewhere new, while the settled stack stays put.

So the rule is:

1. **what appeared** — cells that are filled in the latest frame and were empty in the
   previous one: the piece's leading edge, and the only part of the frame that is
   known to be the piece;
2. **the piece is the connected shape that appeared** — the 4-connected component of
   the latest frame containing those cells. Taking the whole component, not just the
   new cells, is what recovers a piece that partially overlaps where it just was;
3. **what did not appear is terrain** — the latest frame minus the piece;
4. **the motion vector** — the same operation on the previous frame (cells that
   *vanished*), so the piece's centroid can be compared between the two frames:
   ``[drow, dcol]``, which is the gravity direction and its sign.

Why not the per-cell mode over several frames (the plan's first phrasing): with two
frames — and two is all a 2-3 fps inner loop can rely on — the mode of a cell the
piece occupies in *both* frames is "filled", so an L-piece dropping one row has its
whole horizontal bar absorbed into the terrain. Measured against the plan's own test,
that rule cannot pass it.

A single frame claims no piece at all: with the stack and the piece merged there is
no honest way to tell which cells are which, and inventing a piece would hand the
policy a board with a hole in it.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

EMPTY = "0"
FILLED = "1"


def split_pieces(matrices: Sequence[Sequence[str]]) -> dict[str, Any]:
    """``{board_matrix, current_piece_positions, current_piece_mask, motion_vector}``.

    ``matrices`` is a short history of ``read_matrix`` outputs, oldest first; the last
    two carry the motion.
    """
    grids = [_as_grid(matrix) for matrix in matrices]
    if not grids:
        return _result([], [], None)

    latest = grids[-1]
    if len(grids) < 2:
        return _result(latest, [], None)

    previous = grids[-2]
    appeared = _difference(latest, previous)
    vanished = _difference(previous, latest)
    if not appeared or not vanished:
        # Either nothing moved into view, or nothing left it. A piece that moved has
        # both a before and an after; a board that only gained cells changed for a
        # reason this module cannot see (a piece locking plus a spawn, a line clear,
        # an ad). Claiming that whole region as *the piece* would hand the policy a
        # 16-cell "tetromino" - measured on the three real reference frames, which is
        # exactly what they look like read one after another.
        return _result(latest, [], None)

    piece = _component_containing(latest, appeared)
    settled = _minus(latest, piece)
    before = _component_containing(previous, vanished)
    motion = _displacement(before, piece) if before else None
    return _result(settled, piece, motion)


def _result(board, piece: list[tuple[int, int]], motion: list[int] | None) -> dict[str, Any]:
    ordered = sorted(piece)
    return {
        "board_matrix": _as_strings(board),
        "current_piece_positions": [[row, column] for row, column in ordered],
        "current_piece_mask": _mask(ordered),
        "motion_vector": motion,
    }


def _as_grid(matrix: Iterable[str]) -> list[list[bool]]:
    return [[cell not in (EMPTY, ".", " ", "") for cell in row] for row in matrix]


def _as_strings(grid) -> list[str]:
    return ["".join(FILLED if cell else EMPTY for cell in row) for row in grid]


def _difference(newer, older) -> set[tuple[int, int]]:
    """Cells filled in ``newer`` and empty in ``older``."""
    return {
        (row, column)
        for row, line in enumerate(newer)
        for column, filled in enumerate(line)
        if filled and not older[row][column]
    }


def _minus(grid, piece) -> list[list[bool]]:
    cells = set(piece)
    return [
        [False if (row, column) in cells else filled for column, filled in enumerate(line)]
        for row, line in enumerate(grid)
    ]


def _component_containing(grid, seeds: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """The largest 4-connected filled component of ``grid`` that contains a seed."""
    seen: set[tuple[int, int]] = set()
    best: list[tuple[int, int]] = []
    height, width = len(grid), len(grid[0])
    for seed in sorted(seeds):
        if seed in seen or not grid[seed[0]][seed[1]]:
            continue
        component: list[tuple[int, int]] = []
        stack = [seed]
        seen.add(seed)
        while stack:
            row, column = stack.pop()
            component.append((row, column))
            for neighbour in (
                (row - 1, column),
                (row + 1, column),
                (row, column - 1),
                (row, column + 1),
            ):
                r, c = neighbour
                if 0 <= r < height and 0 <= c < width and grid[r][c] and neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        if len(component) > len(best):
            best = component
    return best


def _mask(cells: list[tuple[int, int]]) -> list[list[int]]:
    """The piece as a 0/1 bitmap of its own bounding box — geometry, not a letter."""
    if not cells:
        return []
    top = min(row for row, _ in cells)
    left = min(column for _, column in cells)
    bottom = max(row for row, _ in cells)
    right = max(column for _, column in cells)
    mask = [[0] * (right - left + 1) for _ in range(bottom - top + 1)]
    for row, column in cells:
        mask[row - top][column - left] = 1
    return mask


def _displacement(before: list[tuple[int, int]], after: list[tuple[int, int]]) -> list[int]:
    """``[drow, dcol]`` between two shapes' centroids, rounded to whole cells."""
    return [
        _round(
            sum(cell[axis] for cell in after) / len(after)
            - sum(cell[axis] for cell in before) / len(before)
        )
        for axis in (0, 1)
    ]


def _round(value: float) -> int:
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


__all__ = ["split_pieces"]
