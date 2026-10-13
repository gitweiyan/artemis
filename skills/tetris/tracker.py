"""A falling piece tracked once, instead of re-derived from scratch every frame.

The frame difference (:mod:`pieces`) is the honest answer to "what moved between these
two frames", and it is also why the policy kept arriving late. It re-identifies the
piece every frame, from scratch, so any frame it cannot re-identify is a frame with no
piece: the spawn frame (a new piece appears while the old one locks — nothing vanished),
the shape reveal (the game shows the piece over two frames, so the bitmap changes), and
the frame before a lock (the piece touches the stack, so the connected component is the
piece *plus* the stack). On the device a piece is visible for only one or two frames at
all (``calibration.json`` → ``mechanics.fall_speed``: ~2 rows/s median, faster in bursts),
so losing those frames loses the piece.

This tracker keeps an identity instead:

* **acquire** — when no piece is in flight, use the frame difference once to recognise
  one: the strict separation when it succeeds, otherwise the connected component that
  newly appeared, as long as the whole component appeared (a partial appearance is a
  piece that is still moving, not a new one). The shape is compared to
  ``mechanics.shapes`` once, for the record (the set is a measured lower bound, so an
  unrecognised shape is tracked all the same);
* **track** — from then on the piece is "the cells filled now that were not in the
  terrain measured at acquisition". That is true while the piece touches the stack,
  which is exactly the frame the frame difference refuses, and it does not compare a
  stored bitmap, so a rotation or a row-by-row reveal does not break it;
* **release** — when no such cells remain (the piece is merged into the stack), when
  they have grown beyond any piece (a line clear moved the terrain), or when they are
  unchanged from the previous frame (a falling piece always moves; standing still means
  it has locked). A hard drop that lands the piece between two captures releases without
  ever having tracked it, which is allowed.

The tracker is cross-frame state. ``reset()`` clears it, and
:func:`perception.reset_history` calls that, so a new session never inherits the last
one's piece.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .pieces import split_pieces

EMPTY = "0"
FILLED = "1"
#: A piece is at most this many cells (``calibration.json`` → ``mechanics.shapes``: the
#: game's pieces are 1-4 cells). A non-terrain region larger than this is not a piece.
DEFAULT_MAX_PIECE_CELLS = 4
#: A falling piece always moves between captures; this many identical frames in a row
#: means it has locked. One frame of grace absorbs a capture that caught it mid-cell.
DEFAULT_MAX_IDLE_FRAMES = 1
#: Where to look for the piece next, in cells from where it was. The median fall is ~2 rows
#: per capture, but the fastest mode plus a long capture gap reaches 4+ (device 2026-10-13,
#: captures/play/20261013T091013Z f49->f50: a rotated piece fell 3-4 rows). At 2 the tracker
#: released it and its move was lost - 3 of 6 rotate decisions - and the piece landed at its
#: spawn column, which is the "no strategy" the operator saw; at 8 the recorded trace loses
#: none. A legitimate fast drop is still caught by the frame-difference fallback below.
DEFAULT_MAX_ROW_DELTA = 8
#: A shift can be several taps in one frame; the same local bound keeps a spawn elsewhere
#: on the board from being mistaken for the tracked piece.
DEFAULT_MAX_COL_DELTA = 4


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


def _minus(grid, cells: Iterable[tuple[int, int]]) -> list[str]:
    removed = set(cells)
    return _as_strings(
        [
            [False if (row, column) in removed else filled for column, filled in enumerate(line)]
            for row, line in enumerate(grid)
        ]
    )


def _component_containing(grid, seeds: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """The largest 4-connected filled component of ``grid`` containing a seed."""
    seen: set[tuple[int, int]] = set()
    best: set[tuple[int, int]] = set()
    height, width = len(grid), len(grid[0])
    for seed in sorted(seeds):
        if seed in seen or not grid[seed[0]][seed[1]]:
            continue
        component: set[tuple[int, int]] = set()
        stack = [seed]
        seen.add(seed)
        while stack:
            row, column = stack.pop()
            component.add((row, column))
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


def _components(allowed: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """4-connected components of a set of cells."""
    remaining = set(allowed)
    components: list[set[tuple[int, int]]] = []
    while remaining:
        seed = next(iter(remaining))
        component: set[tuple[int, int]] = set()
        stack = [seed]
        remaining.discard(seed)
        while stack:
            row, column = stack.pop()
            component.add((row, column))
            for neighbour in (
                (row - 1, column),
                (row + 1, column),
                (row, column - 1),
                (row, column + 1),
            ):
                if neighbour in remaining:
                    remaining.discard(neighbour)
                    stack.append(neighbour)
        components.append(component)
    return components


def _centroid(cells) -> tuple[float, float]:
    cells = list(cells)
    return (
        sum(row for row, _ in cells) / len(cells),
        sum(column for _, column in cells) / len(cells),
    )


def _distance(a, b) -> float:
    ca, cb = _centroid(a), _centroid(b)
    return (ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2


def _delta(a, b) -> tuple[float, float]:
    ca, cb = _centroid(a), _centroid(b)
    return (cb[0] - ca[0], cb[1] - ca[1])


def _same_shape(before, after) -> bool:
    """Whether two cell sets are the same shape, up to rotation.

    The same geometry test :mod:`pieces` uses to accept a frame-difference separation,
    reused here so the fast-drop fallback only continues the piece it was tracking.
    """
    from .simulator import normalise, rotate

    if not before or not after or len(before) != len(after):
        return False
    target = normalise(tuple((column, row) for row, column in after))
    current = normalise(tuple((column, row) for row, column in before))
    for _ in range(4):
        if current == target:
            return True
        current = normalise(rotate(current))
    return False


def _mask(cells) -> list[list[int]]:
    """The piece as a 0/1 bitmap of its own bounding box — geometry, not a letter."""
    cells = list(cells)
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


def _round(value: float) -> int:
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def _displacement(before, after) -> list[int]:
    """``[drow, dcol]`` between two cell sets' centroids, rounded to whole cells."""
    origin, target = _centroid(before), _centroid(after)
    return [_round(target[axis] - origin[axis]) for axis in (0, 1)]


class PieceTracker:
    """Stateful: ``push(matrix) -> dict`` for one frame, ``reset()`` for a new session.

    ``matrix`` is a raw :func:`cells.read_matrix` board (stack plus falling piece). The
    return value carries the same four keys :func:`pieces.split_pieces` does — the
    settled ``board_matrix``, the piece's positions and mask, the ``motion_vector`` —
    plus the lifecycle's ``piece_id`` and how long it has been tracked.
    """

    def __init__(
        self,
        calibration: dict[str, Any] | None = None,
        *,
        max_piece_cells: int | None = None,
        max_idle_frames: int | None = None,
        max_row_delta: int | None = None,
        max_col_delta: int | None = None,
    ):
        block = (calibration or {}).get("tracker") or {}
        self.max_piece_cells = int(
            max_piece_cells or block.get("max_piece_cells") or DEFAULT_MAX_PIECE_CELLS
        )
        self.max_idle_frames = int(
            max_idle_frames
            if max_idle_frames is not None
            else block.get("max_idle_frames") or DEFAULT_MAX_IDLE_FRAMES
        )
        self.max_row_delta = float(
            max_row_delta
            if max_row_delta is not None
            else block.get("max_row_delta") or DEFAULT_MAX_ROW_DELTA
        )
        self.max_col_delta = float(
            max_col_delta
            if max_col_delta is not None
            else block.get("max_col_delta") or DEFAULT_MAX_COL_DELTA
        )
        #: The measured shape set is a lower bound (see ``mechanics.shapes``); it is used
        #: to name an acquisition, never to reject one.
        self._shapes = self._shape_index((calibration or {}).get("mechanics") or {})
        self.reset()

    # -- lifecycle ---------------------------------------------------------- #

    def reset(self) -> None:
        """Forget everything: a new session must not inherit the last one's piece."""
        self._terrain: list[str] | None = None
        self._piece: set[tuple[int, int]] | None = None
        self._id: int | None = None
        self._next_id = 1
        self._frames = 0
        self._idle = 0
        self._previous_raw: list[str] | None = None
        self._events: list[dict[str, Any]] = []

    @property
    def active_id(self) -> int | None:
        return self._id

    def events(self) -> list[dict[str, Any]]:
        """Every released lifecycle: its id, how many frames it was tracked, why it ended."""
        return list(self._events)

    # -- the interface ------------------------------------------------------ #

    def push(self, matrix: Sequence[str]) -> dict[str, Any]:
        raw = [str(row) for row in matrix]
        grid = _as_grid(raw)

        if self._piece is not None:
            return self._push_tracking(raw, grid)

        acquired = self._acquire(grid, raw)
        self._previous_raw = raw
        if acquired is None:
            return self._no_piece(raw)
        cells, terrain, motion = acquired
        self._terrain = terrain
        self._piece = set(cells)
        self._id = self._next_id
        self._next_id += 1
        self._frames = 1
        self._idle = 0
        return self._result(raw, cells, motion)

    # -- internals ---------------------------------------------------------- #

    def _push_tracking(self, raw: list[str], grid) -> dict[str, Any]:
        component = self._track(grid, raw)
        if component is None or len(component) > self.max_piece_cells:
            reason = "lost" if component is None else "merged"
            self._release(reason)
            self._previous_raw = raw
            return self._no_piece(raw, released=reason)
        if set(component) == self._piece:
            # A falling piece moves between captures. Standing still means it has locked;
            # a single frame of grace absorbs a capture that caught it mid-cell.
            self._idle += 1
            if self._idle >= self.max_idle_frames:
                self._release("locked")
                self._previous_raw = raw
                return self._no_piece(raw, released="locked")
            motion = [0, 0]
        else:
            self._idle = 0
            # Same guard as `pieces`: only a translated bitmap is a direction of fall. A turn
            # moves the centroid sideways and used to switch the policy's gravity to "right".
            motion = (
                _displacement(self._piece, component)
                if _mask(self._piece) == _mask(component)
                else None
            )
        self._piece = set(component)
        self._frames += 1
        self._previous_raw = raw
        return self._result(raw, component, motion)

    def _track(self, grid, raw: list[str]) -> set[tuple[int, int]] | None:
        """Cells filled now that were not in the terrain: the piece, wherever it is.

        This is the whole trick. The stack never enters ``allowed`` because it was in the
        terrain at acquisition, so a piece resting against the stack is still a separate
        component — unlike the connected-component rule in :mod:`pieces`, which merges
        them and reports nothing.

        The local match is bounded by the measured displacement (a few cells per capture).
        A larger, legitimate jump — a fast drop — is caught by the frame-difference
        fallback, which only accepts a *same-shaped* piece: a new piece that spawned
        elsewhere, or one that has already locked, is a different shape and releases the
        identity instead of stealing it.
        """
        if self._terrain is None:
            return None
        terrain = _as_grid(self._terrain)
        allowed = {
            (row, column)
            for row, line in enumerate(grid)
            for column, cell in enumerate(line)
            if cell and not terrain[row][column]
        }
        components = _components(allowed)
        within = [
            component
            for component in components
            if len(component) <= self.max_piece_cells and self._within_window(component)
        ]
        if within:
            return min(within, key=lambda component: _distance(component, self._piece))
        if self._previous_raw is not None:
            split = split_pieces([self._previous_raw, raw])
            if split["current_piece_positions"]:
                cells = {tuple(position) for position in split["current_piece_positions"]}
                if len(cells) <= self.max_piece_cells and _same_shape(self._piece, cells):
                    return cells
        return None

    def _within_window(self, component) -> bool:
        row_delta, col_delta = _delta(self._piece, component)
        return abs(row_delta) <= self.max_row_delta and abs(col_delta) <= self.max_col_delta

    def _acquire(self, grid, raw: list[str]):
        """Recognise a new piece once: strict separation first, then a whole appearance."""
        if self._previous_raw is not None:
            split = split_pieces([self._previous_raw, raw])
            if split["current_piece_positions"]:
                cells = [tuple(position) for position in split["current_piece_positions"]]
                return set(cells), split["board_matrix"], split["motion_vector"]
        if self._previous_raw is None:
            return None
        previous = _as_grid(self._previous_raw)
        appeared = _difference(grid, previous)
        if not appeared:
            return None
        component = _component_containing(grid, appeared)
        if not component or len(component) > self.max_piece_cells:
            return None
        if set(appeared) != set(component):
            # Only part of the component is new: this is a piece already in flight (a
            # rotation, a shift, a fall caught mid-cell), not a piece arriving.
            return None
        return set(component), _minus(grid, component), None

    def _release(self, reason: str) -> None:
        if self._id is not None:
            self._events.append({"id": self._id, "frames": self._frames, "reason": reason})
        self._piece = None
        self._id = None
        self._terrain = None
        self._frames = 0
        self._idle = 0

    def _result(self, raw: list[str], cells, motion: list[int] | None) -> dict[str, Any]:
        ordered = sorted(cells)
        return {
            "board_matrix": _minus(_as_grid(raw), ordered),
            "current_piece_positions": [[row, column] for row, column in ordered],
            "current_piece_mask": _mask(ordered),
            "motion_vector": motion,
            "piece_id": self._id,
            "piece_frames": self._frames,
            "piece_shape": self._shape_name(ordered),
        }

    def _no_piece(self, raw: list[str], released: str | None = None) -> dict[str, Any]:
        return {
            "board_matrix": list(raw),
            "current_piece_positions": [],
            "current_piece_mask": [],
            "motion_vector": None,
            "piece_id": None,
            "piece_frames": 0,
            "piece_shape": None,
            "released": released,
        }

    # -- shape naming (for the record, never a gate) ------------------------ #

    @staticmethod
    def _shape_index(mechanics: dict[str, Any]) -> dict[tuple, str]:
        from .simulator import normalise, rotate

        index: dict[tuple, str] = {}
        for name, bitmap in (mechanics.get("shapes") or {}).items():
            cells = tuple(
                (row, column)
                for row, line in enumerate(bitmap)
                for column, value in enumerate(line)
                if value
            )
            current = normalise(cells)
            for _ in range(4):
                index[tuple(sorted(current))] = name
                current = normalise(rotate(current))
        return index

    def _shape_name(self, cells) -> str | None:
        if not cells:
            return None
        from .simulator import normalise

        return self._shapes.get(tuple(sorted(normalise(tuple(cells)))))


__all__ = ["PieceTracker"]
