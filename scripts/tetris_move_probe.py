#!/usr/bin/env python3
"""Does one tap really move the piece one column? — measured on the device.

The T8 retrospective (``docs/superpowers/plans/2026-10-13-jev-decision-redesign.md`` §11.8)
found that when the policy planned a move to the **right**, the piece landed up to several
columns short (right moves: 10/20 undershoot, 0/20 overshoot), while left moves mostly hit.
That is the difference between "the stack grew on the side we aimed for" and "the pieces pile
up in the middle", so it has to be pinned down before touching the policy.

Two mechanisms produce the same symptom and this separates them:

* **a lost tap** — the game ignores one of the taps, so ``N`` taps move ``N-k`` columns;
* **the drop is processed too soon** — the move taps and the ``drop`` tap go out in the same
  frame, and the piece locks before the last shift lands.

So the probe sends ``N`` taps on one button and **does not drop** (the default), reads the
piece's column again, and compares. If the shift is short here, taps are lost; if it is right
here but short in the game (where the drop follows immediately), the drop is the problem.

It uses the same pieces the play script does — the real capture, the real ``perception`` and
the game's own four buttons from ``calibration.gestures`` — so the reading is the device's, not
a mock's. Correctness is the operator's to judge; this only writes down the measurement.

Usage:
    .venv/bin/python scripts/tetris_move_probe.py --serial EUWSOBXCZLPFNJF6 --trials 24
    .venv/bin/python scripts/tetris_move_probe.py --serial ... --direction right --with-drop
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SKILLS = PROJECT_ROOT / "skills"
SKILL = "tetris"


def _play_module():
    """The play script's own round-start logic, imported rather than copied."""
    spec = importlib.util.spec_from_file_location(
        "tetris_play", PROJECT_ROOT / "scripts" / "tetris_play.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _button(calibration: dict, name: str) -> tuple[float, float]:
    screen = calibration["screen"]
    ratio = calibration["gestures"][name]["at_ratio"]
    return (ratio[0] * screen["width"], ratio[1] * screen["height"])


def _column(state) -> int | None:
    positions = (state or {}).get("current_piece_positions") or []
    return min(int(c) for _, c in positions) if positions else None


def _board_cells(state) -> set[tuple[int, int]]:
    matrix = (state or {}).get("board_matrix") or []
    return {
        (row, column)
        for row, line in enumerate(matrix)
        for column, cell in enumerate(line)
        if cell == "1"
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serial", required=True)
    parser.add_argument("--trials", type=int, default=24)
    parser.add_argument("--direction", choices=("left", "right"), default="right")
    parser.add_argument(
        "--with-drop",
        action="store_true",
        help="append the drop tap (the game's real move); off isolates tap reliability",
    )
    parser.add_argument(
        "--immediate-drop",
        action="store_true",
        help="like --with-drop but drop with no pause after the taps, as the policy does",
    )
    parser.add_argument("--max-taps", type=int, default=4)
    parser.add_argument("--settle-s", type=float, default=0.15)
    args = parser.parse_args(argv)

    from artemis.reflex.capture import default_capture
    from artemis.reflex.contract import Gesture
    from artemis.reflex.control import AdbInputControl
    from artemis.reflex.registry import load_skill

    load_skill(SKILL, directory=SKILLS)
    panel = importlib.import_module(f"artemis_skills_{SKILL}.panel")
    perception = importlib.import_module(f"artemis_skills_{SKILL}.perception")
    calibration = json.loads((SKILLS / SKILL / "calibration.json").read_text(encoding="utf-8"))
    play = _play_module()

    device = default_capture(args.serial, prefer="screencap")
    actuator = AdbInputControl(args.serial)
    point = _button(calibration, f"move_{args.direction}")
    drop = _button(calibration, "drop")
    print(
        f"== move_probe: {args.direction} 按钮 {point}, taps 1..{args.max_taps}, "
        f"drop={args.with_drop} ==",
        flush=True,
    )
    try:
        route = play.ensure_playable(
            panel,
            perception,
            calibration,
            play._Starter(device, actuator),
            args.serial,
            fresh=True,
        )
        print(f"  前置: {route}", flush=True)

        results = []
        attempts = 0
        while len(results) < args.trials and attempts < args.trials * 4:
            attempts += 1
            state = perception.parse(device.grab(), calibration)
            before = _column(state)
            if before is None:
                continue  # no piece this frame (just locked / panel); try again
            mask = (state or {}).get("current_piece_mask") or []
            width = (
                max(c for row in mask for c, cell in enumerate(row) if cell)
                - min(c for row in mask for c, cell in enumerate(row) if cell)
                + 1
                if mask
                else 1
            )
            taps = 1 + (len(results) % args.max_taps)
            expected = taps if args.direction == "right" else -taps
            # Taps that would push the piece through the wall measure the wall, not the tap.
            if before + expected > 10 - width or before + expected < 0:
                continue
            for _ in range(taps):
                actuator.dispatch(Gesture.tap(*point, description=f"move {args.direction}"))
            landed = None
            if args.immediate_drop:
                # Exactly what the policy does: the drop tap goes out the instant the last
                # shift tap does, with the game having had no frame to apply it.
                actuator.dispatch(Gesture.tap(*drop, description="drop"))
                time.sleep(0.5)
                landed_state = perception.parse(device.grab(), calibration)
                new_cells = _board_cells(landed_state) - _board_cells(state)
                landed = min((c for _, c in new_cells), default=None)
                move = None if landed is None else landed - before
                hit = move == expected
                results.append(
                    {"taps": taps, "before": before, "move": move, "hit": hit, "landed": landed}
                )
                print(
                    f"  trial {len(results):>2}: 起点列 {before} --{taps} taps+立刻 drop--> "
                    f"落定列 {landed} (实际 {move}, 期望 {expected:+d}) "
                    f"{'OK' if hit else 'SHORT'}",
                    flush=True,
                )
                continue
            time.sleep(args.settle_s)
            after_state = perception.parse(device.grab(), calibration)
            after = _column(after_state)
            move = None if after is None else after - before
            if args.with_drop:
                before_cells = _board_cells(state)
                actuator.dispatch(Gesture.tap(*drop, description="drop"))
                time.sleep(0.5)
                landed_state = perception.parse(device.grab(), calibration)
                new_cells = _board_cells(landed_state) - before_cells
                landed = min((c for _, c in new_cells), default=None)
            hit = move == expected
            results.append(
                {
                    "taps": taps,
                    "before": before,
                    "after": after,
                    "move": move,
                    "hit": hit,
                    "landed": landed,
                }
            )
            tail = "" if landed is None else f" 落定列={landed}"
            move_text = "块已落定" if move is None else f"{move:+d}"
            print(
                f"  trial {len(results):>2}: 起点列 {before} --{taps} taps--> {after} "
                f"(实际 {move_text}, 期望 {expected:+d}) {'OK' if hit else 'SHORT / 丢了 tap'}{tail}",
                flush=True,
            )
    finally:
        device.close()

    usable = [r for r in results if r["move"] is not None]
    by_taps: dict[int, list[int]] = {}
    for row in usable:
        by_taps.setdefault(row["taps"], []).append(row["move"])
    print("\n== 汇总 ==", flush=True)
    for taps in sorted(by_taps):
        moves = by_taps[taps]
        print(f"  {taps} taps → 位移 {sorted(moves)}", flush=True)
    if usable:
        print(
            f"  {len(usable)} 次有效；{sum(1 for r in usable if r['hit'])} 次与 tap 数一致",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
