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

import json
import os
import time

from typing import Any

from artemis.reflex.contract import Action
from artemis.services import systemone

from . import jev_state
from . import stall as stall_module
from . import policy as search_policy
from . import simulator as sim

#: Defaults for a calibration file that does not describe the Jev backend yet.
DEFAULT_MODE = "noul"
#: Whether the play message carries the five computed numbers per candidate. On by default
#: since the T7 benchmark: with them the same question went from 25.39 mean regret and no
#: top-1 hits to 0.22 with four of five (plan §4).
DEFAULT_CONSEQUENCES = True
DEFAULT_MIN_CONFIDENCE = 0.05
DEFAULT_OPTIONS_CAP = 40
#: One attempt, not the service's default of three. A timeout is not a transient failure
#: here: the piece keeps falling while the retry runs, so the retry spends the decision
#: budget and the search fallback (which is designed to play) never gets its turn. Measured
#: on the device 2026-10-13: a 2.5 s timeout with three attempts cost ~9 s.
DEFAULT_MAX_ATTEMPTS = 1
MODES = ("noul", "choice")

#: One question per candidate, and it *names* the candidate: the first version asked
#: "is this the best placement?" eighteen times without ever binding "this", and came
#: back flat (0.34-0.37) — an unanswerable question answered with a constant.
_NOUL_INSTRUCTIONS = (
    "Would {identifier} (rotation {rotation}, leftmost cell landing in column {column})"
    " give the best resulting stack of all the candidates: most lines cleared, fewest"
    " holes, lowest and flattest surface?"
)
_CHOICE_INSTRUCTIONS = (
    "Which single placement is best for the current piece, judged by the resulting"
    " stack (fewest holes, lowest height, flattest surface, most lines cleared)?"
)

_telemetry: dict[str, Any] = {"calls": 0, "fallbacks": 0, "reuses": 0, "last_error": None}

#: The decision in force for the piece on screen, and which of its two parts is left to
#: execute. One Jev call per piece: the turn and the placement are two *frames* of the
#: same decision, not two decisions (``see _piece_key`` and the module docstring).
_last: dict[str, Any] = {
    "key": None,
    "placement": None,
    "source": None,
    "shapes": (),
    "phase": None,
    "rotate_frames": 0,
    #: The bitmap the last batch of rotate taps was issued from. A frame whose mask differs
    #: is the turn still animating; one that matches is the turn stopped (or a missed tap).
    "turn_mask": None,
    #: Whether this piece has already had its one drop. A hold frame after the shift must not
    #: drop again (the piece locked ~0.1 s after the shift; a second drop is a no-op at best).
    "dropped": False,
    #: The tracker's lifecycle id, so the flags above can be per *piece* rather than per
    #: decision (a reveal changes the mask and the key, but not the piece).
    "piece_id": None,
}


def reset_telemetry() -> None:
    """Zero the counters: a new session starts counting from nothing.

    Kept separate from :func:`forget_decision` because the counters are the session's
    *report* — the only record of how many paid calls it made. The stop hook used to zero
    them too, which wiped the report at the moment anyone would read it (a 60 s session
    that answered from Jev five times reported ``calls: 0``).
    """
    _telemetry.update({"calls": 0, "fallbacks": 0, "reuses": 0, "last_error": None})


def forget_decision() -> None:
    """Forget the decision in force — a new session or a new round must not replay it."""
    _last.update(
        {
            "key": None,
            "placement": None,
            "source": None,
            "shapes": (),
            "phase": None,
            "rotate_frames": 0,
            "turn_mask": None,
            "dropped": False,
            "piece_id": None,
        }
    )


def reset_decisions() -> None:
    """Both, for a caller that wants the old behaviour (an offline test, say)."""
    reset_telemetry()
    forget_decision()


def telemetry() -> dict[str, Any]:
    """``calls`` (answers read), ``fallbacks`` (decisions the search played), reuses, error.

    ``reuses`` is what makes the plan's budget checkable on a device: one Jev decision per
    piece means the number of calls stays near the number of pieces, not the frames.
    """
    return dict(_telemetry)


def decide(state, calibration):
    """``decide(state, calibration) -> Action`` — the reflex runtime's contract."""
    cal = calibration or {}
    counters = dict(state.get("counters") or {})
    board = _board(state)
    mask = state.get("current_piece_mask") or []

    # A playfield that has stopped moving ends the session before any decision is asked
    # for: there is nothing to place, and asking Jev about a frozen board would spend a
    # call to learn that. The reading, not a conclusion, is what goes into the note — the
    # outer loop decides whether "still for 5 s" means the round is over.
    stopped = stall_module.stalled(state)
    if stopped:
        return Action(
            done=True,
            state={"counters": counters, "note": "board static", "stall": stopped},
        )

    if not board:
        return _delegate(state, cal, counters)
    if not mask:
        return _without_a_piece(state, cal, counters)

    gravity = jev_state.gravity_direction(
        state.get("motion_vector"), _mechanics(cal).get("gravity_direction")
    )
    mode = _mode(cal)
    candidates = _candidates(board, mask, gravity, cal)
    if not candidates:
        counters["topped_out"] = 1
        return Action(done=True, state={"counters": counters, "placement": None})

    key = _piece_key(state, gravity)
    # One plan per *piece*. The key was the proxy for "same piece", but a mask reveal or a
    # board change moves it, and the piece was then planned (and shifted) again - device
    # 2026-10-13 run13 (search-only): plan c9 became right-5, drop, left-7, drop, left-7,
    # drop, landing at column 0. The tracker's id is the real identity; the key is the
    # fallback for a state without one.
    same_piece = state.get("piece_id") is not None and state.get("piece_id") == _last.get(
        "piece_id"
    )
    if _last["placement"] is not None and (same_piece or (key is not None and key == _last["key"])):
        _telemetry["reuses"] += 1
        counters["reused"] = 1
        counters["source"] = _last["source"]
        placement = _last["placement"]
        if _last.get("phase") == "hold":
            # The piece has been turned once and shifted once: from here it is left alone
            # until it lands. The operator's rule, and the fix for what the device showed —
            # turn, shift, turn, shift — because both halves used to re-measure every frame
            # and a mask that reads a column over kept sending the piece back to the buttons.
            return _placement_action(placement, state, counters, mode, settle_only=True)
        if _last.get("phase") == "rotate":
            taps = _taps_to_reach(mask, _last["shapes"], placement.rotation)
            if taps:
                if sim.mask_bitmap(mask) == _last.get("turn_mask"):
                    # The turn has stopped changing and the orientation is still wrong: a tap
                    # really was missed. Issue the taps still missing and start the wait over.
                    return _turning_action(placement, state, counters, mode, "jev", taps, cal)
                # The game animates a turn over more than one capture (measured 2026-10-13,
                # captures/play/20261013T084956Z: a two-tap turn showed one step 0.55 s later),
                # so re-measuring now and adding taps overshoots - it walked 0->1->3, asked for
                # three more, and the rotate guard ended the session. Observe, add nothing.
                _last["turn_mask"] = sim.mask_bitmap(mask)
                return _settling_action(placement, state, counters, mode)
            _last["phase"] = "move"
        # Second half: drive it to the chosen column, using the column this frame shows -
        # the device turns the piece about its second cell, so the column after the turn is
        # not the column before. Re-measured every frame, so a tap that repeated or missed
        # is corrected rather than silently landing in the wrong place; and with no hard
        # drop the piece keeps falling, which is exactly what leaves those frames available.
        _last["phase"] = "move"
        return _placement_action(placement, state, counters, mode)

    message = jev_state.build_state(
        board_matrix=state.get("board_matrix") or _as_matrix(board),
        pieces=state,
        mechanics=_mechanics(cal),
        next_preview=state.get("next_preview"),
        candidates=candidates,
        consequences=bool(_jev_options(cal).get("consequences", DEFAULT_CONSEQUENCES)),
    )
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
        # The search played this decision. Counting it here - where it happens, not where
        # the answer was read - is what keeps `source` and `fallbacks` from disagreeing.
        _telemetry["fallbacks"] += 1
        counters["source"] = "search"
        return _decided(mask, key, chosen, "search", counters, mode, state, cal)

    counters["source"] = "jev"
    counters["mode"] = mode
    return _decided(mask, key, chosen, "jev", counters, mode, state, cal)


def _decided(mask, key, chosen, source: str, counters: dict, mode: str, state, cal):
    """Turn first, on this frame; placement follows on the next one.

    When nothing needs turning (the chosen orientation is the one already on screen - a
    square, or the piece is already the right way up) the placement happens on this frame:
    there is no measurement to wait for, and a wasted frame is a wasted frame.
    """
    shapes = tuple(sim.orientations_from_mask(mask))
    taps = _taps_to_reach(mask, shapes, chosen.rotation)
    _remember(
        key,
        chosen,
        source,
        shapes=shapes,
        phase="rotate" if taps else "hold",
        piece_id=state.get("piece_id"),
    )
    if not taps:
        return _placement_action(chosen, state, counters, mode)
    action = _turning_action(chosen, state, counters, mode, source, taps, cal)
    if not action.done:
        action.state["note"] = f"{source} {mode} r{chosen.rotation}c{chosen.column} (turn first)"
    return action


def _placement_action(placement, state, counters: dict, mode: str, *, settle_only: bool = False):
    """Shift the piece to the chosen column once, then hold until it lands.

    **Once.** The first version re-measured the column every frame and corrected any
    difference, which sounds careful and behaved badly: a turn moves the piece's mask a
    column (the device pivots on its second cell) and a split can read it a cell over, so
    "you are not where the plan says" kept flipping and the piece was walked left, right,
    left while it fell. The operator's rule — one rotation, one shift — is also the fix:
    the shift is computed from the frame the turn finished on, issued (as many taps as the
    distance needs, all in that frame), and after that the piece is left alone.
    """
    current = search_policy.piece_column(state)
    delta = None if settle_only or current is None else int(placement.column) - int(current)
    if settle_only:
        counters["phase"] = "hold"
        counters["on_target"] = 1
        # The shift already went out, and so did its drop. Only an on-target frame that
        # somehow got here without one gets another.
        drop = not _last.get("dropped")
        if drop:
            _last["dropped"] = True
        return Action(
            gestures=(),
            done=False,
            state={
                "placement": placement.as_dict(),
                "phase": "hold",
                "drop": drop,
                "counters": counters,
                "current_column": current,
                "note": f"jev {mode} (turned and shifted; letting it fall)",
            },
        )
    if not delta:
        counters["phase"] = "hold"
        counters["on_target"] = 1
        # On target on the decision frame: this is the frame that locks it — unless it was
        # already dropped. A piece that stays on target holds for several frames (device
        # 2026-10-13 run11 id7: dropped f55, f56 and f57), so the flag must be consulted here
        # exactly as the settle path does; this branch used to re-drop every frame.
        drop = not _last.get("dropped")
        if drop:
            _last["dropped"] = True
        _last["phase"] = "hold"
        return Action(
            gestures=(),
            done=False,
            state={
                "placement": placement.as_dict(),
                "phase": "hold",
                "drop": drop,
                "counters": counters,
                "current_column": current,
                "note": f"jev {mode} (on target; letting it fall)",
            },
        )
    counters["phase"] = "move"
    counters["move_columns"] = delta
    # The phase is recorded as done before the taps go out: this frame issues them, and the
    # frames after it hold. That is what makes it one shift per piece.
    _last["phase"] = "hold"
    # ... and one drop per piece: this frame shifts and locks. The hold frames must not drop
    # again (`control` reads this flag).
    _last["dropped"] = True
    return Action(
        gestures=(),
        done=False,
        state={
            "placement": placement.as_dict(),
            "phase": "move",
            "move_columns": delta,
            "drop": True,
            "counters": counters,
            "current_column": current,
            "note": f"jev {mode} (move {delta:+d} to column {placement.column})",
        },
    )


#: How many frames one piece may spend turning before the loop gives up on it. Measured on
#: the device: eight frames in a row asked for two more rotations each and the terrain never
#: changed once, which is 5 s of a 45 s session spent tapping a button that was getting
#: nowhere. The count is per decision, so an ordinary turn (one or two frames) never sees it.
DEFAULT_MAX_ROTATE_FRAMES = 3


def _turning_action(placement, state, counters: dict, mode: str, source: str, taps: int, cal: dict):
    """The rotate-phase frame, or an end-of-session when the turn is not happening.

    A tap the game ignores and a shape reading that never matches look identical from here —
    the loop cannot tell which, and continuing to tap burns the round. So it stops and says
    what it measured, exactly like the still-board reading: the caller can look at the screen
    and decide (this is what the outer loop is for).
    """
    frames = int(_last.get("rotate_frames") or 0) + 1
    _last["rotate_frames"] = frames
    #: Remember the bitmap these taps were issued from: the next frame compares against it
    #: to tell "still animating" from "stopped (or the tap was missed)".
    _last["turn_mask"] = sim.mask_bitmap(state.get("current_piece_mask") or [])
    limit = int(cal.get("max_rotate_frames") or DEFAULT_MAX_ROTATE_FRAMES)
    if frames > limit:
        counters["rotate_frames"] = frames
        return Action(
            done=True,
            state={
                "counters": counters,
                "note": "the piece is not turning",
                "placement": placement.as_dict(),
                "rotate_frames": frames,
                "rotate_taps": taps,
            },
        )
    counters["phase"] = "rotate"
    counters["rotate_taps"] = taps
    counters["rotate_frames"] = frames
    return Action(
        gestures=(),
        done=False,
        state={
            "placement": placement.as_dict(),
            "phase": "rotate",
            "rotate_taps": taps,
            "counters": counters,
            "current_column": search_policy.piece_column(state),
            "note": f"{source} {mode} (still turning the same piece)",
        },
    )


def _settling_action(placement, state, counters: dict, mode: str):
    """A frame that observes a turn without adding taps to it.

    The game animates one turn over more than one capture, so the remaining-taps count read
    on the very next frame is not final. Tapping again here is what walked a J piece
    0->1->3 and tripped the rotate guard; this frame lets the batch that was already sent
    land, and the next frame decides from the settled orientation.
    """
    counters["phase"] = "rotate"
    counters["rotate_taps"] = 0
    return Action(
        gestures=(),
        done=False,
        state={
            "placement": placement.as_dict(),
            "phase": "rotate",
            # Zero, or `control` would re-derive taps from `placement.rotation` on a rotate
            # frame and send them again.
            "rotate_taps": 0,
            "counters": counters,
            "current_column": search_policy.piece_column(state),
            "note": f"jev {mode} (letting the turn settle)",
        },
    )


def _taps_to_reach(mask, shapes, target: int) -> int:
    """How many clockwise taps take the shape now on screen to ``shapes[target]``.

    Counted from what is on screen rather than assumed: the device may still be showing a
    half-finished turn, and a missed tap would otherwise leave the piece in the wrong
    orientation while the placement phase measured its column as if it had turned.
    """
    observed = tuple(tuple(row) for row in sim.mask_bitmap(mask))
    for index, shape in enumerate(shapes or ()):
        if tuple(tuple(row) for row in shape) == observed:
            return (int(target) - index) % max(1, len(shapes))
    return int(target)


def _piece_key(state, gravity: str):
    """What makes two decisions *the same decision*: the board, the gravity, the piece.

    Neither the piece's row nor its **orientation** nor its column is part of it: where a
    piece lands does not depend on the row it is in, the two-phase execution turns it
    between frames, and a key that changed on every turn asked Jev a second time for the
    same piece — measured, that breaks the plan's "one call per piece" budget.
    """
    mask = state.get("current_piece_mask") or []
    if not mask:
        return None
    return (
        tuple(state.get("board_matrix") or ()),
        gravity,
        tuple(
            sorted(tuple(tuple(row) for row in shape) for shape in sim.orientations_from_mask(mask))
        ),
    )


def _remember(key, placement, source, *, shapes=(), phase=None, piece_id=None) -> None:
    """Remember the decision in force — and start its turn count from zero.

    ``dropped`` rides on the piece, not the decision: a reveal changes the mask (and so the
    key) without changing the piece, and a second decision on the same piece must not drop
    it again (device 2026-10-13 run12 id3). A new piece resets it.
    """
    same_piece = piece_id is not None and piece_id == _last.get("piece_id")
    _last.update(
        {
            "key": key,
            "placement": placement,
            "source": source,
            "shapes": shapes,
            "phase": phase,
            "rotate_frames": 0,
            "turn_mask": None,
            "dropped": _last.get("dropped", False) if same_piece else False,
            "piece_id": piece_id,
        }
    )


# --------------------------------------------------------------------------- #
# Asking
# --------------------------------------------------------------------------- #


def _closeness(mode: str, raw: Any) -> dict[str, Any]:
    """How close the decision was: runner-up, margin, and the next-best candidates.

    One number cannot answer "was it a hard call". Under ``noul`` the answers are independent
    probabilities (they do not sum to one), under ``choice`` they are shares of a normalised
    distribution — so the record says which kind it is, and a reader can tell "wrong and sure"
    from "wrong and unsure" without guessing what the number meant.
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    scored: list[tuple[float, str]] = []
    for name, answer in raw.items():
        try:
            scored.append((systemone.probability(answer), str(name)))
        except systemone.SystemOneUnavailable:
            continue
    if not scored:
        return {}
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    best = scored[0][0]
    runner_up = scored[1][0] if len(scored) > 1 else best
    return {
        "distribution": "normalized" if mode == "choice" else "independent",
        "best": scored[0][1],
        "confidence": round(best, 4),
        "runner_up": round(runner_up, 4),
        "margin": round(best - runner_up, 4),
        "alternatives": [name for _, name in scored[1:4]],
    }


def _ask(mode, message, candidates, cal, counters):
    """``(placement, confidence)`` from Jev, or ``(None, None)`` for the search to play.

    Every call can be recorded — the message that went out, the answers that came back, what
    was chosen and how long it took — by pointing ``ARTEMIS_JEV_DUMP`` at a file (the play
    script sets it to ``<run>/jev.jsonl``). Without it the payload exists only while it is in
    flight: the trace can say ``source=jev`` and which placement won, but not what the model
    was asked or what it answered, which is the first thing to look at when a decision is
    wrong.
    """
    timeout = cal.get("systemone_timeout_s")
    attempts = int(_jev_options(cal).get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    started = time.monotonic()
    raw: Any = None
    # The questions are a separate argument to the service, so the record has to collect them
    # here: what the model was asked is half of the input.
    record: dict[str, Any] = {}
    try:
        placement, confidence, raw = (
            _choice(message, candidates, timeout, record, attempts)
            if mode == "choice"
            else _noul(message, candidates, timeout, record, attempts)
        )
    except systemone.SystemOneUnavailable as exc:
        # Counted where the search then plays (in `decide`), so `source` and `fallbacks`
        # cannot disagree.
        _telemetry["last_error"] = str(exc)
        counters["jev_fallback"] = str(exc)[:120]
        _dump_jev(
            mode,
            message,
            candidates,
            None,
            None,
            raw,
            seconds=time.monotonic() - started,
            failure=str(exc),
            record=record,
            closeness=_closeness(mode, raw),
        )
        return None, None
    _telemetry["calls"] += 1
    if placement is None:
        counters["jev_fallback"] = "abstained"
    _dump_jev(
        mode,
        message,
        candidates,
        placement,
        confidence,
        raw,
        seconds=time.monotonic() - started,
        record=record,
        closeness=_closeness(mode, raw),
    )
    return placement, confidence


def _dump_jev(
    mode,
    message,
    candidates,
    placement,
    confidence,
    raw,
    *,
    seconds,
    failure=None,
    record=None,
    closeness=None,
):
    """Append one call to ``ARTEMIS_JEV_DUMP``: the input, the output and the outcome.

    Off by default (a library call should not leave surprise files behind); switched on by
    the play script, which is where a human reviews decisions. A failure to write must never
    break a decision.
    """
    path = os.environ.get("ARTEMIS_JEV_DUMP")
    if not path:
        return
    try:
        questions = (record or {}).get("questions")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "t": round(time.time(), 3),
                        "mode": mode,
                        "seconds": round(seconds, 3),
                        "candidates": len(candidates),
                        "input": message,
                        "questions": questions,
                        "question_count": len(questions) if isinstance(questions, dict) else None,
                        "answers": raw,
                        "chosen": None
                        if placement is None
                        else {"rotation": placement.rotation, "column": placement.column},
                        "confidence": confidence,
                        "failure": failure,
                        **{k: v for k, v in (closeness or {}).items() if k != "confidence"},
                    },
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )
    except (OSError, TypeError, ValueError):
        pass


def _noul(message, candidates, timeout, record=None, max_attempts=1):
    questions = {
        f"cand_{index}": {
            "type": "noul",
            "instructions": _NOUL_INSTRUCTIONS.format(
                identifier=_identifier(candidates[index][0]),
                rotation=candidates[index][0].rotation,
                column=candidates[index][0].column,
            ),
        }
        for index in range(len(candidates))
    }
    if record is not None:
        record["questions"] = questions
    answers = systemone.ask(message, questions, timeout=timeout, max_attempts=max_attempts)
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
        return None, None, answers
    best = max(probability for probability, _ in scored)
    index = min(index for probability, index in scored if probability >= best)
    return candidates[index][0], best, answers


def _choice(message, candidates, timeout, record=None, max_attempts=1):
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
        max_attempts=max_attempts,
    )
    if winner is None or winner not in identifiers:
        return None, None, distribution
    index = identifiers.index(winner)
    confidence = distribution.get(winner)
    return (
        candidates[index][0],
        (float(confidence) if confidence is not None else None),
        distribution,
    )


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
