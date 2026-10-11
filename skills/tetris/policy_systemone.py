"""The System One (Jev) policy: pick a placement with typed questions, not chat.

Shape of the call, and why:

* **candidates come from** :mod:`simulator` — the model never invents a move, so an
  illegal placement is impossible by construction;
* **state is text**: the board as rows, the piece, the next piece and the candidate
  list. Jev takes no images, so perception's CV output is the interface;
* **one request per piece**, not per frame: the loop only needs a decision when the
  piece changes, and a request per frame would buy nothing;
* **fan-out**: one ``noul`` question per candidate in a single call (the vendor's
  documented "speculative fan-out" pattern), then the code takes the argmax. Ties
  are broken by the deterministic search score, so the ordering never depends on
  network jitter.

Falling back is not an afterthought: no key, a ``429``, a timeout or a bad answer
all mean "use the search policy". The counters record which one played, so a report
can tell a model game from a fallback game.
"""

from __future__ import annotations

from typing import Any

from artemis.reflex.contract import Action
from artemis.services import systemone

from . import policy as search_policy
from . import simulator as sim

#: Cap on candidates per request: keeps the state and the question fan-out small.
MAX_CANDIDATES = 40
#: Below this probability the model's answer is treated as no better than the
#: search's own ranking (and the search's tie-break decides).
MIN_CONFIDENCE = 0.05

_telemetry: dict[str, Any] = {"calls": 0, "fallbacks": 0, "last_error": None}


def reset_telemetry() -> None:
    _telemetry.update({"calls": 0, "fallbacks": 0, "last_error": None})


def telemetry() -> dict[str, Any]:
    return dict(_telemetry)


def _render_state(
    board: sim.Board, piece: str, next_piece: str | None, candidates
) -> dict[str, Any]:
    """The state as text: the board as rows, plus what the question needs."""
    return {
        "board_rows_top_to_bottom": ["".join(row) for row in board],
        "note": (
            "'.' is empty, letters are settled tetrominoes. Rows are top to bottom,"
            " columns left to right. The current piece enters from the top."
        ),
        "current_piece": piece,
        "next_piece": next_piece,
        "candidate_moves": [
            {
                "id": f"r{placement.rotation}c{placement.column}",
                "rotation": placement.rotation,
                "column": placement.column,
            }
            for placement, _ in candidates
        ],
    }


def decide(state, calibration):
    """``policy.decide`` with the System One backend, falling back to search."""
    cal = calibration or {}
    board = state.get("board") or []
    piece = state.get("current")
    if not board or not piece:
        return search_policy.decide(state, cal)

    counters = dict(state.get("counters") or {})
    next_piece = state.get("next")
    candidates = sim.legal_placements(board, piece)[:MAX_CANDIDATES]
    if not candidates:
        return search_policy.decide(state, cal)

    chosen = None
    try:
        answers = systemone.ask(
            _render_state(board, piece, next_piece, candidates),
            {
                f"cand_{index}": {
                    "type": "noul",
                    "instructions": (
                        "Is this the best placement for the current piece, judged by the"
                        " resulting stack (fewest holes, lowest height, flattest surface,"
                        " most lines cleared)?"
                    ),
                }
                for index in range(len(candidates))
            },
            timeout=cal.get("systemone_timeout_s"),
        )
        scored: list[tuple[float, int]] = []
        for index in range(len(candidates)):
            answer = answers.get(f"cand_{index}")
            if answer is None:
                continue
            try:
                scored.append((systemone.probability(answer), index))
            except systemone.SystemOneUnavailable:
                continue
        if scored:
            best_probability, best_index = max(scored)
            chosen = candidates[best_index][0] if best_probability >= MIN_CONFIDENCE else None
            counters["model_best_probability"] = round(best_probability, 3)
        _telemetry["calls"] += 1
    except systemone.SystemOneUnavailable as exc:
        _telemetry["fallbacks"] += 1
        _telemetry["last_error"] = str(exc)
        counters["systemone_fallback"] = str(exc)[:120]
        logger_note = f"System One unavailable, search policy plays: {exc}"
        chosen = None
    else:
        logger_note = ""

    if chosen is None:
        # The model abstained (or was unsure): the deterministic ranking decides.
        placement, detail = search_policy.choose(
            board, piece, weights=cal.get("weights"), next_piece=next_piece
        )
        if placement is None:
            return Action(done=True, state={"counters": counters})
        counters["source"] = "search"
        return Action(
            gestures=(),
            done=False,
            state={"placement": placement.as_dict(), "counters": counters, "note": logger_note},
        )

    counters["source"] = "systemone"
    return Action(
        gestures=(),
        done=False,
        state={
            "placement": chosen.as_dict(),
            "counters": counters,
            "note": f"systemone r{chosen.rotation}c{chosen.column}",
        },
    )


__all__ = ["MAX_CANDIDATES", "MIN_CONFIDENCE", "decide", "reset_telemetry", "telemetry"]
