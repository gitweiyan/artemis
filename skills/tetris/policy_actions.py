"""Playing by actions: ask Jev which of the four buttons to press, then press it.

Why this exists next to :mod:`policy_jev`
-----------------------------------------
``policy_jev`` decides a *placement* — a rotation and a column — and the device then needs a
turn on one frame and a shift on the next, which is what made the execution window the binding
constraint on this device: measured, about two usable frames per piece, with the plan
regularly arriving after the piece had already landed (``captures/play/*/trace.jsonl``).

The reference implementation (``third_party/Agent-JEV-Tetris-main``) asks a different
question: *which single action next*, over the seven legal moves, each option carrying what it
does and what it leads to. The game's four buttons are exactly that action space, so one
decision is one tap — and the model never has to simulate the drop, which is the part its own
documentation says it is bad at.

The session-end rules (:mod:`stall`, the game-over panel) are the same as the other policies',
and so is the fallback: when the API cannot be reached we play **our own** plan's first action
rather than nothing, because a decision with no gesture is a wasted frame, and frames are the
scarce resource here.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from artemis.reflex.contract import Action
from artemis.services import systemone

from . import jev_state, simulator as sim, stall as stall_module

MODES = ("choice", "noul")
DEFAULT_MODE = "choice"
#: What the fallback plays when there is no plan to follow.
FALLBACK_ACTION = "drop"

_telemetry: dict[str, Any] = {"calls": 0, "fallbacks": 0, "last_error": None}
#: The last action taken, for the message's "this undoes your last move" warning.
_last: dict[str, Any] = {"action": None}


def reset_telemetry() -> None:
    _telemetry.update({"calls": 0, "fallbacks": 0, "last_error": None})
    _last["action"] = None


def telemetry() -> dict[str, Any]:
    return dict(_telemetry)


def decide(state, calibration):
    """``decide(state, calibration) -> Action`` — the reflex runtime's contract.

    The action rides in ``action.state["action"]``; :mod:`control` turns it into exactly one
    tap. The plan (if the calibration asks for one) rides along for the caller's log, never as
    a gesture of its own.
    """
    cal = calibration or {}
    counters = dict(state.get("counters") or {})
    stopped = stall_module.stalled(state)
    if stopped:
        return Action(
            done=True, state={"counters": counters, "note": "board static", "stall": stopped}
        )

    board = _board(state)
    mask = state.get("current_piece_mask") or []
    if not board or not mask:
        return Action()

    positions = state.get("current_piece_positions") or []
    column = min((int(c) for _, c in positions), default=0)
    mode = _mode(cal)
    message = jev_state.build_action_state(
        board_matrix=state.get("board_matrix") or board,
        pieces=state,
        mechanics=cal.get("mechanics") or {},
        next_preview=state.get("next_preview"),
        rotation=0,
        column=column,
        last_action=_last.get("action"),
        seconds_per_action=cal.get("seconds_per_action"),
        seconds_per_row=cal.get("seconds_per_row"),
    )
    offered = [entry["id"] for entry in message["candidate_actions"]]
    if not offered:
        return Action()

    chosen, note, source = _ask(mode, message, offered, cal, counters)
    if chosen not in offered:
        plan = message.get("plan") or {}
        chosen = plan.get("next_action") if plan.get("next_action") in offered else FALLBACK_ACTION
        source = "plan" if plan.get("next_action") else "search"
        _telemetry["fallbacks"] += 1
        counters["actions_fallback"] = note or "no usable answer"
    counters["source"] = source
    _last["action"] = chosen
    return Action(
        gestures=(),
        done=False,
        state={
            "action": chosen,
            "calibration": cal,
            "counters": counters,
            "plan": message.get("plan") or None,
            "current_column": column,
            "note": f"{source} {mode} ({chosen})",
        },
    )


def _ask(mode: str, message: dict, offered: list[str], cal: dict, counters: dict):
    """``(action, note, source)``; the action is ``None`` when nothing usable came back."""
    timeout = cal.get("systemone_timeout_s")
    started = time.monotonic()
    criteria = {
        entry["id"]: {
            "does": entry["does"],
            **({"then_lands": entry["then_lands"]} if "then_lands" in entry else {}),
            **({"warning": entry["warning"]} if "warning" in entry else {}),
            **(
                {"this_starts_the_plan": entry["starts_the_plan"]}
                if "starts_the_plan" in entry
                else {}
            ),
        }
        for entry in message["candidate_actions"]
    }
    raw: Any = None
    try:
        if mode == "noul":
            questions = {
                f"act_{name}": {
                    "type": "noul",
                    "instructions": f"Would {name} ({criteria[name]['does']}) give the best resulting stack of all the actions?",
                }
                for name in offered
            }
            raw = systemone.ask(message, questions, timeout=timeout)
            scored = []
            for name in offered:
                answer = raw.get(f"act_{name}")
                if answer is None:
                    continue
                try:
                    scored.append((systemone.probability(answer), name))
                except systemone.SystemOneUnavailable:
                    continue
            chosen = max(scored)[1] if scored else None
        else:
            winner, distribution = systemone.choice(
                message,
                message["instructions"]["task"],
                criteria,
                question_id="action",
                timeout=timeout,
            )
            raw = distribution
            chosen = winner if winner in offered else None
    except systemone.SystemOneUnavailable as exc:
        _telemetry["last_error"] = str(exc)
        _dump(mode, message, raw, None, seconds=time.monotonic() - started, failure=str(exc))
        return None, str(exc)[:120], "search"
    _telemetry["calls"] += 1
    _dump(mode, message, raw, chosen, seconds=time.monotonic() - started)
    if chosen is None:
        counters["actions_fallback"] = "abstained"
    return chosen, None, "jev"


def _board(state):
    matrix = state.get("board_matrix") or []
    if not matrix:
        return []
    return [
        [sim.EMPTY if str(cell) in {"0", ".", ""} else sim.MASK_FILL for cell in row]
        for row in matrix
    ]


def _mode(cal: dict) -> str:
    mode = str((cal.get("jev") or {}).get("mode") or DEFAULT_MODE).lower()
    return mode if mode in MODES else DEFAULT_MODE


def _dump(mode, message, raw, chosen, *, seconds, failure=None) -> None:
    """One line per call in ``ARTEMIS_JEV_DUMP``, the same switch the placement policy uses."""
    path = os.environ.get("ARTEMIS_JEV_DUMP")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "t": round(time.time(), 3),
                        "mode": mode,
                        "space": "action",
                        "seconds": round(seconds, 3),
                        "input": message,
                        "answers": raw,
                        "chosen": chosen,
                        "failure": failure,
                    },
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )
    except (OSError, TypeError, ValueError):
        pass


__all__ = ["MODES", "decide", "reset_telemetry", "telemetry"]
