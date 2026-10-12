"""The Jev policy: rank the moves the simulator allows, play one of them.

The legality boundary is not negotiable and not the model's business. Candidates come
from :mod:`simulator`, the model is asked *which of these* is best, and every answer is
mapped back to the candidate that named it. An id nobody offered, an answer below
``min_confidence``, a 429, a timeout — all of them are "the search plays", never a move
from nowhere. That is what makes this policy safe to point at a real game: the worst
case is a worse move, not an illegal one.

Two question shapes, both the vendor's:

* ``noul`` (default) — one true/false question per candidate ("is this the best
  placement?"), argued in one call as speculative fan-out; the code takes the argmax.
  Ties go to the lower candidate index, which is deterministic.
* ``choice`` — a single question with one criterion per candidate. Fewer tokens, one
  answer, and the winner is an id from the same list.

Falling back is the designed path, not an accident: no key, a rate limit, a bad answer
or a hedging model all mean the deterministic search plays, and ``source`` in the
counters says which one it was. The fallback is a *mask* search rather than
:func:`policy.choose`, because ``choose`` takes a piece letter and assumes downward
gravity — neither holds for a custom shape or a switched gravity (Review Focus 3/4).
It scores with the same features and weights, from the same legal set, in the same
gravity as the model was offered.
"""

from __future__ import annotations

from typing import Any

from artemis.reflex.contract import Action
from artemis.services import systemone

from . import jev_state
from . import policy as search_policy
from . import simulator as sim

#: Defaults for a calibration file that does not describe the Jev backend yet.
DEFAULT_MODE = "noul"
DEFAULT_MIN_CONFIDENCE = 0.05
DEFAULT_OPTIONS_CAP = 40
MODES = ("noul", "choice")

_NOUL_INSTRUCTIONS = (
    "Is this the best placement for the current piece, judged by the resulting stack"
    " (fewest holes, lowest height, flattest surface, most lines cleared)?"
)
_CHOICE_INSTRUCTIONS = (
    "Which single placement is best for the current piece, judged by the resulting"
    " stack (fewest holes, lowest height, flattest surface, most lines cleared)?"
)

_telemetry: dict[str, Any] = {"calls": 0, "fallbacks": 0, "last_error": None}


def reset_telemetry() -> None:
    _telemetry.update({"calls": 0, "fallbacks": 0, "last_error": None})


def telemetry() -> dict[str, Any]:
    """``calls`` (answers read), ``fallbacks`` (decisions the search played), last error."""
    return dict(_telemetry)


def decide(state, calibration):
    """``decide(state, calibration) -> Action`` — the reflex runtime's contract."""
    cal = calibration or {}
    counters = dict(state.get("counters") or {})
    board = _board(state)
    mask = state.get("current_piece_mask") or []

    if not board:
        return _delegate(state, cal, counters)
    if not mask:
        return _without_a_piece(state, cal, counters)

    gravity = jev_state.gravity_direction(
        state.get("motion_vector"), _mechanics(cal).get("gravity_direction")
    )
    candidates = _candidates(board, mask, gravity, cal)
    if not candidates:
        counters["topped_out"] = 1
        return Action(done=True, state={"counters": counters, "placement": None})

    message = jev_state.build_state(
        board_matrix=state.get("board_matrix") or _as_matrix(board),
        pieces=state,
        mechanics=_mechanics(cal),
        next_preview=state.get("next_preview"),
        candidates=candidates,
    )
    mode = _mode(cal)
    chosen, confidence = _ask(mode, message, candidates, cal, counters)
    if chosen is not None and confidence is not None:
        minimum = float(_jev_options(cal).get("min_confidence") or DEFAULT_MIN_CONFIDENCE)
        if confidence < minimum:
            counters["jev_abstained"] = round(float(confidence), 3)
            chosen = None

    if chosen is None:
        chosen = _search(board, mask, gravity, cal)
        if chosen is None:  # pragma: no cover - candidates were not empty
            counters["topped_out"] = 1
            return Action(done=True, state={"counters": counters, "placement": None})
        counters["source"] = "search"
        return Action(
            gestures=(),
            done=False,
            state={"placement": chosen.as_dict(), "counters": counters, "note": "search fallback"},
        )

    counters["source"] = "jev"
    counters["mode"] = mode
    return Action(
        gestures=(),
        done=False,
        state={
            "placement": chosen.as_dict(),
            "counters": counters,
            "note": f"jev {mode} r{chosen.rotation}c{chosen.column}",
        },
    )


# --------------------------------------------------------------------------- #
# Asking
# --------------------------------------------------------------------------- #


def _ask(mode, message, candidates, cal, counters):
    """``(placement, confidence)`` from Jev, or ``(None, None)`` for the search to play."""
    timeout = cal.get("systemone_timeout_s")
    try:
        answer = (
            _choice(message, candidates, timeout)
            if mode == "choice"
            else _noul(message, candidates, timeout)
        )
    except systemone.SystemOneUnavailable as exc:
        _telemetry["fallbacks"] += 1
        _telemetry["last_error"] = str(exc)
        counters["jev_fallback"] = str(exc)[:120]
        return None, None
    _telemetry["calls"] += 1
    if answer == (None, None):
        _telemetry["fallbacks"] += 1
        counters["jev_fallback"] = "abstained"
    return answer


def _noul(message, candidates, timeout):
    questions = {
        f"cand_{index}": {"type": "noul", "instructions": _NOUL_INSTRUCTIONS}
        for index in range(len(candidates))
    }
    answers = systemone.ask(message, questions, timeout=timeout)
    scored: list[tuple[float, int]] = []
    for index in range(len(candidates)):
        answer = answers.get(f"cand_{index}")
        if answer is None:
            continue
        try:
            scored.append((systemone.probability(answer), index))
        except systemone.SystemOneUnavailable:
            continue
    if not scored:
        return None, None
    best = max(probability for probability, _ in scored)
    index = min(index for probability, index in scored if probability >= best)
    return candidates[index][0], best


def _choice(message, candidates, timeout):
    identifiers = [_identifier(placement) for placement, _ in candidates]
    criteria = {
        name: f"rotation {placement.rotation}, column {placement.column}"
        for name, (placement, _) in zip(identifiers, candidates, strict=True)
    }
    winner, distribution = systemone.choice(
        message,
        _CHOICE_INSTRUCTIONS,
        criteria,
        question_id="placement",
        timeout=timeout,
    )
    if winner is None or winner not in identifiers:
        return None, None
    index = identifiers.index(winner)
    confidence = distribution.get(winner)
    return candidates[index][0], (float(confidence) if confidence is not None else None)


def _identifier(placement) -> str:
    return f"r{placement.rotation}c{placement.column}"


# --------------------------------------------------------------------------- #
# Falling back
# --------------------------------------------------------------------------- #


def _search(board, mask, gravity, cal):
    """The deterministic ranking over the same legal set, in the same gravity."""
    weights = cal.get("weights") or search_policy.DEFAULT_WEIGHTS
    best: tuple[float, sim.Placement] | None = None
    for placement, cells in sim.legal_placements(board, mask, gravity=gravity):
        dropped = sim.drop(board, cells, placement.column, gravity=gravity)
        if dropped is None:  # pragma: no cover - legal_placements only offers playable moves
            continue
        settled, cleared = sim.clear_lines(dropped)
        total = search_policy.score_board(settled, cleared, weights)
        if best is None or total > best[0]:
            best = (total, placement)
    return best[1] if best else None


# --------------------------------------------------------------------------- #
# Reading the state
# --------------------------------------------------------------------------- #


def _board(state) -> sim.Board:
    """The board as the simulator wants it, from either state shape.

    The new pipeline hands over ``board_matrix`` (rows of "0"/"1"); the older one hands
    over ``board`` (rows of piece letters and ".").
    """
    legacy = state.get("board")
    if legacy:
        return [list(row) for row in legacy]
    matrix = state.get("board_matrix") or []
    return [
        [sim.EMPTY if str(cell) in {"0", ".", ""} else sim.MASK_FILL for cell in row]
        for row in matrix
    ]


def _as_matrix(board) -> list[str]:
    return ["".join("0" if cell == sim.EMPTY else "1" for cell in row) for row in board]


def _candidates(board, mask, gravity, cal):
    cap = int(_jev_options(cal).get("options_cap") or DEFAULT_OPTIONS_CAP)
    return sim.legal_placements(board, mask, gravity=gravity)[: max(1, cap)]


def _mechanics(cal) -> dict[str, Any]:
    return dict(cal.get("mechanics") or {})


def _jev_options(cal) -> dict[str, Any]:
    return dict(cal.get("jev") or {})


def _mode(cal) -> str:
    mode = str(_jev_options(cal).get("mode") or DEFAULT_MODE).lower()
    return mode if mode in MODES else DEFAULT_MODE


def _without_a_piece(state, cal, counters):
    """No mask in the state: the older letter-based pipeline may still be able to play.

    A letter state is only usable when gravity is downward: ``policy.choose`` drops
    pieces down by construction, so under any other gravity the honest answer is to
    make no move at all rather than to play a move the rules do not allow.
    """
    if state.get("current") and _declared_gravity(state, cal) == "down":
        state = dict(state, counters={**counters, "source": "search"})
        return search_policy.decide(state, cal)
    counters["no_piece"] = int(counters.get("no_piece") or 0) + 1
    return Action(gestures=(), done=False, state={"counters": counters})


def _declared_gravity(state, cal) -> str:
    return jev_state.gravity_direction(
        state.get("motion_vector"), _mechanics(cal).get("gravity_direction")
    )


def _delegate(state, cal, counters):
    """No board at all: the session-end rules (game over, target lines) still apply."""
    return search_policy.decide(dict(state, counters=counters), cal)


__all__ = ["MODES", "decide", "reset_telemetry", "telemetry"]
