#!/usr/bin/env python3
"""What did the session actually do? Read a play trace and report the effect.

``scripts/tetris_play.py`` writes one JSON line per frame: the terrain matrix, the piece's
mask and position, the decision, every gesture, and the stillness reading. This turns that
into the numbers a human wants after watching a round:

* how long it survived, how many decisions and taps it made, and how fast;
* what the stack did — height over time, how much of the board it filled;
* **cleared rows**, as an estimate read off the matrix series rather than off the game's own
  scoreboard: the game displays lines and score, but reading them needs HUD digit templates
  that this skill does not have yet, so the estimate is labelled as one. It counts the
  terrains where a row was complete and, on the next frame, the board was shorter — plus
  every frame-to-frame drop of ten or more cells, which is what a clear looks like;
* how the decisions split between turning, moving and holding, and how many taps each took;
* the pieces: how many new decisions there were (one Jev call each) against how many frames
  reused one.

Usage:
    .venv/bin/python scripts/tetris_analyze.py captures/play/<stamp>/trace.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def filled(matrix) -> int:
    return sum(row.count("1") for row in matrix or [])


def height(matrix) -> int:
    """Rows from the floor to the top-most filled cell (0 = an empty board)."""
    rows = matrix or []
    for index, row in enumerate(rows):
        if "1" in row:
            return len(rows) - index
    return 0


def full_rows(matrix) -> int:
    return sum(1 for row in matrix or [] if row and all(cell == "1" for cell in row))


def cleared_rows(frames: list[dict]) -> dict:
    """Estimate the clears: complete rows that left, plus drops of ten cells or more."""
    events = []
    previous = None
    for frame in frames:
        matrix = frame.get("matrix")
        if not matrix:
            continue
        if previous is not None:
            was_full, now_full = full_rows(previous), full_rows(matrix)
            drop = filled(previous) - filled(matrix)
            if (was_full and not now_full) or drop >= 10:
                events.append(
                    {
                        "t": frame.get("t"),
                        "full_before": was_full,
                        "full_after": now_full,
                        "cells_lost": drop,
                        "height_before": height(previous),
                        "height_after": height(matrix),
                    }
                )
        previous = matrix
    return {"events": events, "estimate": len(events)}


def session_summary(frames: list[dict]) -> dict:
    if not frames:
        return {}
    mats = [f["matrix"] for f in frames if f.get("matrix")]
    decisions = [f.get("action") or {} for f in frames if f.get("action")]
    phases = {}
    for decision in decisions:
        phase = decision.get("phase") or ("done" if decision.get("done") else "none")
        phases[phase] = phases.get(phase, 0) + 1
    gestures = [g for f in frames for g in (f.get("gestures") or [])]
    taps = {}
    for gesture in gestures:
        taps[gesture.get("description") or gesture.get("kind")] = (
            taps.get(gesture.get("description") or gesture.get("kind"), 0) + 1
        )
    pieces = [f for f in frames if (f.get("piece_cells") or 0) > 0]
    # Older traces predate the `reused` flag; saying "unknown" beats inventing a number.
    knows_reuse = any("reused" in d for d in decisions)
    new_decisions = [d for d in decisions if not d.get("reused")] if knows_reuse else None
    cleared = cleared_rows(frames)
    return {
        "seconds": round(frames[-1]["t"] - frames[0]["t"], 1),
        "frames": len(frames),
        "fps": round(len(frames) / max(1e-6, frames[-1]["t"] - frames[0]["t"]), 2),
        "capture_ms_p50": sorted(f.get("frame_ms") or 0 for f in frames)[len(frames) // 2],
        "filled_start": filled(mats[0]) if mats else 0,
        "filled_end": filled(mats[-1]) if mats else 0,
        "filled_peak": max((filled(m) for m in mats), default=0),
        "height_peak": max((height(m) for m in mats), default=0),
        "height_end": height(mats[-1]) if mats else 0,
        "cleared_rows_estimate": cleared["estimate"],
        "clear_events": cleared["events"],
        "phases": phases,
        "taps": taps,
        "gestures": len(gestures),
        "frames_with_a_piece": len(pieces),
        "new_decisions": None if new_decisions is None else len(new_decisions),
        "declined": sum(1 for d in decisions if d.get("note") == "board static"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    frames = load(args.trace)
    summary = session_summary(frames)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(f"trace: {args.trace}")
    print(
        f"  时长 {summary['seconds']}s  帧 {summary['frames']}  fps {summary['fps']}  "
        f"抓帧中位 {summary['capture_ms_p50']}ms"
    )
    print(
        f"  棋盘：起 {summary['filled_start']} 格 → 终 {summary['filled_end']} 格，"
        f"峰值 {summary['filled_peak']} 格 / 最高 {summary['height_peak']} 行（终局 {summary['height_end']} 行）"
    )
    print(f"  消行（估算，基于矩阵序列）: {summary['cleared_rows_estimate']}")
    for event in summary["clear_events"]:
        print(
            f"     t={event['t']:.1f}s  满行 {event['full_before']}→{event['full_after']}  "
            f"少 {event['cells_lost']} 格  高度 {event['height_before']}→{event['height_after']}"
        )
    print(f"  决策相位: {summary['phases']}")
    print(f"  点击 {summary['gestures']} 次: {summary['taps']}")
    new = summary["new_decisions"]
    print(
        f"  见到下落块的帧 {summary['frames_with_a_piece']}  新决策 "
        f"{new if new is not None else '未知（该 trace 早于 reused 字段；以会话输出的 jev.calls 为准）'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
