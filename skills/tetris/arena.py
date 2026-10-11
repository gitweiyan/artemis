"""Offline arena: play thousands of pieces without touching a device.

This is the instrument that answers "is policy A better than policy B?" with
numbers instead of impressions — including for a model-driven policy, which would
otherwise cost device time and real games to evaluate.

Everything here is the *reference* rules from :mod:`simulator`: same piece bag, same
gravity, same clears. A game is fully determined by ``(seed, policy)``, so a
comparison is reproducible and a regression shows up as a different score for the
same seed.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import simulator as sim

#: A policy maps (board, piece, next_piece) to a placement (or None to give up).
Policy = Callable[[sim.Board, str, str | None], sim.Placement | None]


@dataclass
class GameResult:
    seed: int
    pieces: int
    lines: int
    score: int
    max_height: int
    end_reason: str
    decisions_ms: float = 0.0

    @property
    def lines_per_piece(self) -> float:
        return self.lines / self.pieces if self.pieces else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "pieces": self.pieces,
            "lines": self.lines,
            "score": self.score,
            "max_height": self.max_height,
            "end_reason": self.end_reason,
            "decisions_ms": round(self.decisions_ms, 2),
        }


def bag(random_source: random.Random) -> list[str]:
    """One 7-bag (the standard fair randomiser)."""
    pieces = list(sim.PIECE_NAMES)
    random_source.shuffle(pieces)
    return pieces


#: Standard line-clear scoring, level 1.
CLEAR_SCORE = {0: 0, 1: 100, 2: 300, 3: 500, 4: 800}


def play_game(
    policy: Policy,
    *,
    seed: int = 0,
    max_pieces: int = 1000,
    width: int = sim.BOARD_WIDTH,
    height: int = sim.BOARD_HEIGHT,
) -> GameResult:
    """Play one game with ``policy`` and report how it went."""
    source = random.Random(seed)
    board = sim.empty_board(width, height)
    upcoming: list[str] = []
    lines = 0
    score = 0
    max_height = 0
    pieces = 0
    decisions_ms = 0.0
    end_reason = "piece_limit"

    while pieces < max_pieces:
        if len(upcoming) < 2:
            upcoming.extend(bag(source))
        piece = upcoming.pop(0)
        next_piece = upcoming[0] if upcoming else None

        started = time.perf_counter()
        placement = policy(board, piece, next_piece)
        decisions_ms += (time.perf_counter() - started) * 1000.0

        if placement is None:
            end_reason = "no_legal_placement"
            break
        cells = sim.orientations(piece)[placement.rotation]
        dropped = sim.drop(board, piece, cells, placement.column)
        if dropped is None:
            end_reason = "illegal_placement"
            break
        board, cleared = sim.clear_lines(dropped)
        lines += cleared
        score += CLEAR_SCORE.get(cleared, 0)
        pieces += 1
        max_height = max(max_height, max(sim.column_heights(board)))

        if max_height >= height:
            end_reason = "topped_out"
            break

    return GameResult(
        seed=seed,
        pieces=pieces,
        lines=lines,
        score=score,
        max_height=max_height,
        end_reason=end_reason,
        decisions_ms=decisions_ms,
    )


@dataclass
class TournamentResult:
    """Aggregate of many seeded games for one policy."""

    policy: str
    games: list[GameResult] = field(default_factory=list)

    @property
    def total_lines(self) -> int:
        return sum(g.lines for g in self.games)

    @property
    def mean_lines(self) -> float:
        return self.total_lines / len(self.games) if self.games else 0.0

    @property
    def median_lines(self) -> float:
        return _median([g.lines for g in self.games])

    @property
    def mean_decision_ms(self) -> float:
        total = sum(g.decisions_ms for g in self.games)
        pieces = sum(g.pieces for g in self.games)
        return total / pieces if pieces else 0.0

    @property
    def topped_out(self) -> int:
        return sum(1 for g in self.games if g.end_reason == "topped_out")

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "games": len(self.games),
            "mean_lines": round(self.mean_lines, 1),
            "median_lines": round(self.median_lines, 1),
            "min_lines": min((g.lines for g in self.games), default=0),
            "max_lines": max((g.lines for g in self.games), default=0),
            "topped_out": self.topped_out,
            "mean_decision_ms": round(self.mean_decision_ms, 3),
        }


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def search_policy(weights: dict[str, float] | None = None) -> Policy:
    """The deterministic baseline (see :mod:`policy`)."""
    from . import policy as policy_module

    def _play(board: sim.Board, piece: str, next_piece: str | None) -> sim.Placement | None:
        placement, _ = policy_module.choose(board, piece, weights=weights, next_piece=next_piece)
        return placement

    return _play


def run_tournament(
    *,
    games: int = 20,
    seed: int = 1,
    max_pieces: int = 500,
    weights: dict[str, float] | None = None,
    policies: dict[str, Policy] | None = None,
) -> dict[str, Any]:
    """Play ``games`` seeded games for each policy and return the comparison.

    A skill's optional arena entry point: the framework knows nothing about Tetris,
    it just calls this and renders what comes back.
    """
    from . import policy as policy_module

    active: dict[str, Policy] = policies or {
        "search": search_policy(weights if weights is not None else policy_module.DEFAULT_WEIGHTS)
    }
    seed_list = [seed + index for index in range(max(1, int(games)))]
    results: dict[str, Any] = {}
    for name, policy in active.items():
        tournament = TournamentResult(policy=name)
        for game_seed in seed_list:
            tournament.games.append(play_game(policy, seed=game_seed, max_pieces=max_pieces))
        results[name] = tournament.as_dict()
    return {"games_per_policy": len(seed_list), "policies": results}


__all__ = [
    "CLEAR_SCORE",
    "GameResult",
    "Policy",
    "TournamentResult",
    "bag",
    "play_game",
    "run_tournament",
    "search_policy",
]
