#!/usr/bin/env python3
"""Draw the calibrated cell grid over a screenshot, so a human can see the alignment.

This is a **viewer**, not a check: it draws what ``calibration.json`` says the board is and
leaves the judging to your eyes. That matters here — the grid's *pitch* can be measured
without measuring its *position*, and a box 60 px out in y passed every numeric test until
someone looked at the frames.

What you are looking at in the output:

* yellow lines — the cell boundaries implied by the calibration's
  ``origin_x/origin_y/cell_w/cell_h``;
* a cyan rectangle — the calibrated playfield box;
* green dots — the cells ``cells.read_matrix`` calls filled;
* with ``--old``, a red rectangle — the box this skill used before 2026-10-12
  (x[35,465] y[320,1180]), for contrast.

Aligned looks like: every yellow line sitting on the game's own gridline (or on the seam
between two blocks) for the whole height, and every green dot inside a block. Off looks
like: a line drifting across a block, or a line missing the board's border by a row.

Usage:
    .venv/bin/python scripts/grid_overlay.py FRAME.png --out overlay.png
    .venv/bin/python scripts/grid_overlay.py FRAME.png --out overlay.png --old --labels
    # a 4x zoom of one corner, where a sub-pixel error becomes visible:
    .venv/bin/python scripts/grid_overlay.py FRAME.png --out corner.png \\
        --region 30,1040,240,1240 --scale 4 --labels
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: The box this skill used before the 2026-10-12 re-measurement, for contrast.
OLD_BOX = (35, 320, 465, 1180)
YELLOW = (255, 255, 0)
CYAN = (0, 255, 255)
RED = (255, 60, 60)
GREEN = (0, 255, 0)
LABEL = (255, 255, 255)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Screenshot to annotate.")
    parser.add_argument("--out", required=True, help="Where to write the annotated PNG.")
    parser.add_argument("--old", action="store_true", help="Also draw the pre-2026-10-12 box.")
    parser.add_argument("--region", help="Crop to x0,y0,x1,y1 (device pixels) first.")
    parser.add_argument("--scale", type=int, default=1, help="Zoom the result (nearest).")
    parser.add_argument("--labels", action="store_true", help="Write row/column numbers.")
    parser.add_argument("--calibration", help="Calibration file (default: the tetris skill's).")
    args = parser.parse_args()

    import numpy as np
    from PIL import Image, ImageDraw

    from artemis.reflex.registry import load_skill

    calibration = (
        json.loads(Path(args.calibration).read_text(encoding="utf-8"))
        if args.calibration
        else load_skill("tetris", directory=PROJECT_ROOT / "skills").calibration
    )
    modules = {
        name: importlib.import_module(f"artemis_skills_tetris.{name}")
        for name in ("roi", "grid_probe", "cells")
    }

    array = np.asarray(Image.open(args.image).convert("RGB"), dtype="float32")
    x0, y0, x1, y1 = modules["roi"].roi_pixels(array.shape, calibration)
    grid = calibration["grid"]
    cell_w, cell_h = float(grid["cell_w"]), float(grid["cell_h"])
    columns, rows = int(grid["cols"]), int(grid["rows"])
    field = modules["roi"].board_crop(array, calibration)
    problem = modules["grid_probe"].identity_problem(
        modules["grid_probe"].measure_grid(field, calibration),
        modules["grid_probe"].declared_cells(calibration),
    )
    matrix = None if problem else modules["cells"].read_matrix(field, calibration)

    origin_x, origin_y = 0, 0
    if args.region:
        rx0, ry0, rx1, ry1 = (int(v) for v in args.region.split(","))
        array = array[ry0:ry1, rx0:rx1, :]
        origin_x, origin_y = rx0, ry0
    image = Image.fromarray(array.astype("uint8"), "RGB")
    scale = max(1, args.scale)
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale), Image.NEAREST)
    draw = ImageDraw.Draw(image)

    def point(x: float, y: float) -> tuple[int, int]:
        return (round((x - origin_x) * scale), round((y - origin_y) * scale))

    def segment(a: tuple[float, float], b: tuple[float, float], colour, width: int = 1) -> None:
        draw.line([point(*a), point(*b)], fill=colour, width=width)

    for column in range(columns + 1):
        x = grid["origin_x"] + column * cell_w
        segment((x, y0), (x, y1), YELLOW, scale)
        if args.labels and column < columns:
            draw.text(point(x + 3 * scale, y0 + 2 * scale), str(column), fill=LABEL)
    for row in range(rows + 1):
        y = grid["origin_y"] + row * cell_h
        segment((x0, y), (x1, y), YELLOW, scale)
        if args.labels and row < rows:
            draw.text(point(x0 + 2 * scale, y + 2 * scale), str(row), fill=LABEL)

    draw.rectangle([point(x0, y0), point(x1, y1)], outline=CYAN, width=scale)
    if args.old:
        draw.rectangle(
            [point(OLD_BOX[0], OLD_BOX[1]), point(OLD_BOX[2], OLD_BOX[3])],
            outline=RED,
            width=scale,
        )

    drawn_filled = 0
    if matrix:
        for row, line in enumerate(matrix):
            for column, cell in enumerate(line):
                if cell != "1":
                    continue
                drawn_filled += 1
                centre = point(
                    grid["origin_x"] + (column + 0.5) * cell_w,
                    grid["origin_y"] + (row + 0.5) * cell_h,
                )
                radius = max(2, scale * 2)
                draw.ellipse(
                    [
                        centre[0] - radius,
                        centre[1] - radius,
                        centre[0] + radius,
                        centre[1] + radius,
                    ],
                    fill=GREEN,
                )

    image.save(args.out)
    print(f"wrote {args.out}  ({image.width}x{image.height})")
    print(f"  calibrated box x[{x0},{x1}] y[{y0},{y1}]   cells {cell_w}x{cell_h}  {columns}x{rows}")
    print(f"  identity: {problem or 'this board'}   filled cells drawn: {drawn_filled}")
    if args.old:
        print(
            f"  old box (red) x[{OLD_BOX[0]},{OLD_BOX[2]}] y[{OLD_BOX[1]},{OLD_BOX[3]}]"
            f"   difference: dx {OLD_BOX[0] - x0:+d}, dy {OLD_BOX[1] - y0:+d}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
