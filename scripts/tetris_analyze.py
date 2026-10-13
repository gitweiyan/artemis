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
    """Estimate the clears: complete rows that left, plus drops of ten cells or more.

    Never across a round boundary: a new round's board is nearly empty, which looks exactly
    like a clear that took 35 cells with it (and did, once, in this script's own output
    before the trace carried the round number).
    """
    events = []
    previous = None
    previous_round = None
    for frame in frames:
        matrix = frame.get("matrix")
        if not matrix:
            continue
        if frame.get("round") != previous_round:
            previous, previous_round = matrix, frame.get("round")
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
        previous_round = frame.get("round")
    return {"events": events, "estimate": len(events)}


def session_summary(frames: list[dict]) -> dict:
    if not frames:
        return {}
    marked = any(f.get("round") for f in frames)
    per_round: dict = {}
    for frame in frames:
        key = frame.get("round") or 1
        entry = per_round.setdefault(key, {"frames": 0, "from": frame["t"], "to": frame["t"]})
        entry["frames"] += 1
        entry["to"] = frame["t"]
    for entry in per_round.values():
        entry["seconds"] = round(entry["to"] - entry["from"], 1)
        entry["to"] = round(entry["to"], 1)
        entry["from"] = round(entry["from"], 1)
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
        # The span of the whole trace, which includes the waits between rounds; the play
        # time itself is per round (see `rounds`).
        "seconds": round(frames[-1]["t"] - frames[0]["t"], 1),
        "rounds": per_round,
        "rounds_marked": marked,
        "frames": len(frames),
        "fps": round(len(frames) / max(1e-6, frames[-1]["t"] - frames[0]["t"]), 2),
        "capture_ms_p50": sorted(f.get("frame_ms") or 0 for f in frames)[len(frames) // 2],
        "filled_start": filled(mats[0]) if mats else 0,
        "filled_end": filled(mats[-1]) if mats else 0,
        "filled_peak": max((filled(m) for m in mats), default=0),
        "height_peak": max((height(m) for m in mats), default=0),
        "height_end": height(mats[-1]) if mats else 0,
        "cleared_rows_estimate": cleared["estimate"],
        # Without round markers a restart looks exactly like a clear that took the whole
        # board with it, so the number is flagged rather than quietly reported.
        "cleared_rows_reliable": marked,
        "cleared_rows_note": (
            ""
            if marked
            else "该 trace 早于局号字段：跨局边界（换局时棋盘从满变空）可能被算成消行，估算不可靠"
        ),
        "clear_events": cleared["events"],
        "phases": phases,
        "taps": taps,
        "gestures": len(gestures),
        "frames_with_a_piece": len(pieces),
        "new_decisions": None if new_decisions is None else len(new_decisions),
        "declined": sum(1 for d in decisions if d.get("note") == "board static"),
    }


def click_log(frames: list[dict]) -> int:
    """Every gesture the loop sent: `时间 | 帧 | 按键 | 像素 | 耗时 | 决策`.

    The gesture carries its own timestamp when the trace has one (added with the play script's
    per-gesture clock); older traces only have the frame's stamp, which is earlier by the whole
    decision — that column is then marked so a reader does not read it as the tap time.
    """
    rows = 0
    for frame in frames:
        action = frame.get("action") or {}
        for gesture in frame.get("gestures") or []:
            rows += 1
            own = "t" in gesture
            when = gesture["t"] if own else frame.get("t")
            where = gesture.get("points") or []
            where = where[0] if where else None
            print(
                f"{when:8.2f}s{' ' if own else '~'} 第{frame.get('frame'):>3}帧 "
                f"{gesture.get('description') or gesture.get('kind'):<12} {where} "
                f"{gesture.get('ms', 0):6.1f}ms  ← 决策 phase={action.get('phase')} "
                f"落点={action.get('placement')} 来源={action.get('source')}"
            )
    if not rows:
        print("这次运行的 trace 里没有任何点击")
    else:
        missing = sum(1 for f in frames for g in (f.get("gestures") or []) if "t" not in g)
        print(
            f"\n共 {rows} 次点击"
            + (
                f"；其中 {missing} 次没有自己的时间戳（该 trace 早于逐手势时钟），行首标 ~ 的是帧时间"
                if missing
                else "（每条都是点击发生时刻）"
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--clicks",
        action="store_true",
        help="print the control actions: when, which button, where, and the decision behind it",
    )
    args = parser.parse_args(argv)

    frames = load(args.trace)
    if args.clicks:
        return click_log(frames)

    summary = session_summary(frames)
    # Written next to the trace so a run directory is self-describing: the session's own
    # numbers used to exist only on stdout (a /tmp log), which is not a place to keep
    # evidence.
    (args.trace.parent / "analysis.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
    print(
        f"  消行（估算，基于矩阵序列）: {summary['cleared_rows_estimate']}"
        + ("" if summary.get("cleared_rows_reliable") else "  ⚠ " + summary["cleared_rows_note"])
    )
    for key, entry in (summary.get("rounds") or {}).items():
        print(
            f"    第{key}局: 帧 {entry['frames']}  跨度 {entry['seconds']}s  ({entry['from']}→{entry['to']})"
        )
    for event in summary["clear_events"]:
        print(
            f"     t={event['t']:.1f}s  满行 {event['full_before']}→{event['full_after']}  "
            f"少 {event['cells_lost']} 格  高度 {event['height_before']}→{event['height_after']}"
        )
    print(f"  决策相位: {summary['phases']}")
    print(f"  点击 {summary['gestures']} 次: {summary['taps']}")
    new = summary["new_decisions"]
    print(
        f"  见到下落块的帧 {summary['frames_with_a_piece']}  未复用旧决策的帧 "
        f"{new if new is not None else '未知（该 trace 早于 reused 字段）'}"
        "（其中只有一部分是 Jev 调用：回退与无块帧不调用模型，付费次数以会话输出的 jev.calls 为准）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
