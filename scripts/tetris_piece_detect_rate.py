#!/usr/bin/env python3
"""Does the pipeline see every piece the game spawns? Measured per size, offline.

The question came out of a live session whose trace showed pieces of 1, 2, 3 and 4 cells —
this game does not always spawn tetrominoes (the operator confirmed it, and the shapes are
now recorded in ``calibration.json#mechanics.shapes``). The detection rate looked lower than
in earlier runs, and the two candidate explanations are *not* the same problem:

* **the gates reject small pieces** — ``pieces.split_pieces`` needs the two frames' grids to
  match, something to have appeared *and* something to have vanished, and the appeared cells
  to be congruent with a shape;
* **the pieces are simply gone faster** — with the drop button enabled a piece reaches the
  floor in about 0.1 s, so it has fewer frames in which it can be seen at all.

This separates them: a synthetic sweep puts one known shape of each size on a real playfield
and asks whether it is found, and the recorded traces say how long pieces actually stayed
visible before and after the drop button was enabled.

Usage:
    .venv/bin/python scripts/tetris_piece_detect_rate.py
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS = PROJECT_ROOT / "skills"
PLAY = SKILLS / "tetris" / "samples" / "session-play"
WHOLE = {"board_roi_ratios": [0.0, 0.0, 1.0, 1.0]}
#: A colour the cell classifier accepts (V above v_min, S above s_min) and the terrain does not
#: use, so a painted piece cannot be confused with the stack.
PIECE_RGB = (235, 45, 55)
#: Traces to compare: one before the drop button was enabled, one after.
BEFORE = "20261012T141809Z"
AFTER = "20261013T064359Z"


def modules():
    from artemis.reflex.registry import load_skill

    load_skill("tetris", directory=SKILLS)
    return (
        importlib.import_module("artemis_skills_tetris.perception"),
        importlib.import_module("artemis_skills_tetris.cells"),
        json.loads((SKILLS / "tetris" / "calibration.json").read_text(encoding="utf-8")),
    )


def frame_shape(image, box):
    return image[box[1] : box[3], box[0] : box[2]].shape


def paint(image, bitmap, row: int, column: int, box, cell_w: float, cell_h: float):
    """A copy of the frame with ``bitmap`` drawn at cell (row, column)."""
    import numpy as np

    out = image.copy()
    for r, line in enumerate(bitmap):
        for c, cell in enumerate(line):
            if not cell:
                continue
            y0 = int(box[1] + (row + r) * cell_h) + 3
            y1 = int(box[1] + (row + r + 1) * cell_h) - 3
            x0 = int(box[0] + (column + c) * cell_w) + 3
            x1 = int(box[0] + (column + c + 1) * cell_w) - 3
            out[y0:y1, x0:x1, :] = np.asarray(PIECE_RGB, dtype="float32")
    return out


def synthetic_sweep(perception, calibration, shapes, base_path: Path):
    """One known shape at a time: is it found, and with the right size?"""
    import io

    import numpy as np
    from PIL import Image

    with Image.open(base_path) as handle:
        base = np.asarray(handle.convert("RGB"), dtype="float32")
    # The corpus frames are mid-game playfields: they carry their own stack *and* their own
    # piece. Painting onto that made the first version of this sweep report that T and S
    # tetrominoes are never detected — the classifier was reading the base frame's cells as
    # well as the painted ones (measured: (2,4),(2,5),(3,4),(3,5) present with nothing painted
    # there), so the frame difference did not match the shape and the congruence gate correctly
    # refused it. The top of the board is cleared to a flat background so only the painted
    # shape can account for a difference.
    top = int(12 * calibration["grid"]["cell_h"])
    base[:top, :, :] = np.median(base[:top].reshape(-1, 3), axis=0)
    box = (0, 0, base.shape[1], base.shape[0])
    grid = calibration["grid"]
    cell_w, cell_h = grid["cell_w"], grid["cell_h"]
    settings = {**calibration, **WHOLE}
    results = []

    for bitmap in shapes:
        height = len(bitmap)
        width = len(bitmap[0])
        size = sum(sum(line) for line in bitmap)
        found = missed = 0
        for row in (1, 2, 3):
            for column in (1, 5, 10 - width - 1):
                if column < 0:
                    continue
                first = paint(base, bitmap, row, column, box, cell_w, cell_h)
                second = paint(base, bitmap, row + 1, column, box, cell_w, cell_h)
                frames = []
                for array in (first, second):
                    buffer = io.BytesIO()
                    Image.fromarray(array.astype("uint8"), "RGB").save(buffer, "JPEG", quality=92)
                    frames.append(buffer.getvalue())
                perception.reset_history()
                perception.parse(frames[0], settings)
                state = perception.parse(frames[1], settings)
                mask = (state or {}).get("current_piece_mask") or []
                cells = sum(sum(line) for line in mask)
                if cells == size:
                    found += 1
                else:
                    missed += 1
                    results.append(
                        {"shape": bitmap, "size": size, "row": row, "column": column, "saw": cells}
                    )
        print(
            f"  {size} 格  {str(bitmap):28s} 检出 {found}/{found + missed}"
            + ("" if not missed else f"   ← 漏检 {missed} 次")
        )
    return results


def trace_runs(path: Path) -> dict:
    """How long a piece stays visible, per piece, in a recorded run."""
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    runs, current = [], []
    for row in rows:
        if (row.get("piece_cells") or 0) > 0:
            current.append(row)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    sizes = [max((r.get("piece_cells") or 0) for r in run) for run in runs]
    return {
        "frames": len(rows),
        "piece_frames": sum(1 for r in rows if (r.get("piece_cells") or 0) > 0),
        "runs": len(runs),
        "run_lengths": sorted(len(run) for run in runs),
        "sizes": sorted(sizes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", default=str(PLAY / "frame-0015.jpg"), help="a real playfield to paint on"
    )
    args = parser.parse_args(argv)

    perception, _cells, calibration = modules()
    shapes = list(calibration["mechanics"]["shapes"].values())
    print(f"参与扫描的形状 {len(shapes)} 个（来自 calibration.mechanics.shapes，实测记录）\n")
    print("A. 合成扫描：把已知形状画在真实棋盘上，看管线是否检出（3 行 × 3 列位置 各一次）")
    missed = synthetic_sweep(perception, calibration, shapes, Path(args.base))

    print("\nB. 真实 trace 里的'块可见帧串'（每串 = 一个块被看到的那几帧）")
    for label, stamp in (("启用硬降之前", BEFORE), ("启用硬降之后", AFTER)):
        path = PROJECT_ROOT / "captures" / "play" / stamp / "trace.jsonl"
        if not path.exists():
            print(f"  {label}: 没有 {path}")
            continue
        info = trace_runs(path)
        print(
            f"  {label} ({stamp}): {info['piece_frames']}/{info['frames']} 帧见到块，"
            f"{info['runs']} 个块，每块可见帧数 {info['run_lengths']}，块大小 {info['sizes']}"
        )
    print(
        "\n判读：若 A 里各尺寸都检出，则'检出率下降'不是门的锅，而是块可见时间变短（B 的每块帧数）；"
        "若 A 里小块被漏检，则是形状一致性/出现-消失两门之一在拒小块。"
    )
    if missed:
        print(f"\n漏检明细（前 10 条）：{json.dumps(missed[:10], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
