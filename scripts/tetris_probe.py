#!/usr/bin/env python3
"""What is on the screen right now, in numbers — for debugging taps by hand.

Reading a screenshot by eye answers "did something happen?"; it does not answer "did the
panel go away, or did the screen just dim?". On this game those look alike: a modal scrim
dims everything to 0.61x while the panel's own pixels stay identical, so the panel check
still matches (a normalised correlation is immune to a uniform dimming) and only the
numbers show which of the two happened.

So each probe reports, in one line and then as JSON:

* **brightness** and the **title band's** brightness — 88 is the panel as it normally
  renders, ~54 is the same panel behind a scrim;
* the **panel score** (1.000 on the panel, ≤0.05 on a played frame at threshold 0.6);
* what ``perception.parse`` makes of it: ``game_over``, a board of CxR, or nothing;
* the **stall reading** the inner loop would act on (similarity, how long the playfield
  has held still, and whether that already ends the session);
* whether a **scrim** is present, and where the un-dimmed region is (the dialog or ad on
  top of it);
* the accessibility tree's clickable nodes, which is the only text on this screen the
  repository can read without a vision model.

Usage:
    .venv/bin/python scripts/tetris_probe.py --serial EUWSOBXCZLPFNJF6
    .venv/bin/python scripts/tetris_probe.py --serial ... --tap 492 1015 --wait 15
    .venv/bin/python scripts/tetris_probe.py --serial ... --save captures/probe/after-left.png

``--tap`` sends one tap through the same control path a session uses, waits, and probes —
so what it measures is what the inner loop would have seen. Nothing else sends input.
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SKILLS = PROJECT_ROOT / "skills"
SKILL = "tetris"
#: The panel as it normally renders; below this the screen is behind a scrim.
TITLE_BAND_UNDIMMED = 88.1
TITLE_BAND_DIMMED = 53.6
#: Where the title box is, in the 720x1600 screenshots this was measured on.
TITLE_BAND = (slice(552, 632), slice(273, 446))


def wrong_interpreter() -> str | None:
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return None
    if Path(sys.executable).resolve() == venv_python.resolve():
        return None
    return str(venv_python)


def load_modules():
    from artemis.reflex.registry import load_skill

    load_skill(SKILL, directory=SKILLS)
    return (
        importlib.import_module(f"artemis_skills_{SKILL}.panel"),
        importlib.import_module(f"artemis_skills_{SKILL}.perception"),
    )


def screenshot(serial: str) -> bytes:
    completed = subprocess.run(
        ["adb", "-s", serial, "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=30,
        check=False,
    )
    return completed.stdout


def tap(serial: str, x: int, y: int) -> None:
    from artemis.reflex.contract import Gesture
    from artemis.reflex.control import AdbInputControl

    AdbInputControl(serial).dispatch(Gesture.tap(x, y, description=f"({x},{y})"))


def accessibility(serial: str) -> list[dict]:
    """Clickable nodes with their text and bounds — the only readable text on this screen."""
    subprocess.run(
        ["adb", "-s", serial, "shell", "uiautomator", "dump", "/sdcard/probe-ui.xml"],
        capture_output=True,
        timeout=30,
        check=False,
    )
    dump = subprocess.run(
        ["adb", "-s", serial, "shell", "cat", "/sdcard/probe-ui.xml"],
        capture_output=True,
        timeout=30,
        check=False,
    ).stdout.decode("utf-8", "replace")
    nodes = []
    for piece in dump.split("<node"):
        if 'clickable="true"' not in piece:
            continue
        text = piece.split('text="', 1)[-1].split('"', 1)[0] if 'text="' in piece else ""
        bounds = piece.split('bounds="', 1)[-1].split('"', 1)[0] if 'bounds="' in piece else ""
        if bounds and bounds != "[0,0][0,0]":
            nodes.append({"text": text, "bounds": bounds})
    return nodes


def scrim(image, calibration: dict, panel_module) -> dict:
    """Is a scrim dimming the screen, and where is the un-dimmed thing on top of it?

    The panel corpus is the "undimmed" reference: dividing this frame by it pixel-wise
    gives ~0.61 wherever a scrim is, and ~1.0 where a dialog's own pixels are drawn.
    """
    import numpy as np

    np_module = np
    reference_path = SKILLS / SKILL / "samples" / "panel-now.png"
    if not reference_path.exists():
        return {"detected": None, "reason": "no reference frame in the skill's samples"}
    from PIL import Image

    with Image.open(reference_path) as handle:
        reference = np_module.asarray(handle.convert("RGB"), dtype="float32").mean(axis=2)
    grey = image.mean(axis=2)
    if grey.shape != reference.shape:
        return {"detected": None, "reason": "this frame is not the calibrated screen size"}
    valid = reference > 40
    if not valid.any():
        return {"detected": None, "reason": "the reference has nothing bright enough"}
    ratio = grey[valid] / np_module.maximum(reference[valid], 1e-6)
    median = float(np_module.median(ratio))
    undimmed = valid & (grey / np_module.maximum(reference, 1e-6) > median * 1.35)
    rows = np_module.nonzero(undimmed.any(axis=1))[0]
    box = None
    if rows.size:
        columns = np_module.nonzero(undimmed.any(axis=0))[0]
        box = [int(columns.min()), int(rows.min()), int(columns.max()), int(rows.max())]
    return {
        "detected": bool(abs(median - 1.0) > 0.15),
        "ratio": round(median, 3),
        "undimmed_box_px": box,
    }


def probe(serial: str, calibration: dict, save: Path | None = None, *, fast: bool = False) -> dict:
    import numpy as np
    from PIL import Image

    panel_module, perception_module = load_modules()
    raw = screenshot(serial)
    if not raw:
        return {"ok": False, "reason": "no screenshot (is the device awake?)"}
    if save:
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_bytes(raw)

    with Image.open(__import__("io").BytesIO(raw)) as handle:
        image = np.asarray(handle.convert("RGB"), dtype="float32")

    state = perception_module.parse(raw, calibration)
    if state is None:
        parsed = "not a board"
    elif state.get("game_over"):
        parsed = "game_over"
    else:
        matrix = state.get("board_matrix") or []
        parsed = f"board {len(matrix[0])}x{len(matrix)}" if matrix else "board (empty)"

    report = {
        "ok": True,
        "brightness": round(float(image.mean()), 1),
        "title_band": round(float(image[TITLE_BAND].mean()), 1),
        "dimmed": bool(float(image[TITLE_BAND].mean()) < 70.0),
        "panel_score": round(panel_module.score(raw, calibration), 4),
        "panel_threshold": calibration["panel"]["match_threshold"],
        "is_game_over_panel": panel_module.is_game_over(raw, calibration),
        "parse": parsed,
        "stall": state.get("stall") if state else None,
        "scrim": scrim(image, calibration, panel_module),
        # The accessibility dump costs 1-2 s (it starts a UI server); `--fast` leaves it
        # out so the probe can be run in a loop while tapping by hand.
        "clickable": [] if fast else accessibility(serial),
    }
    return report


def summarise(report: dict) -> str:
    if not report.get("ok"):
        return f"probe failed: {report.get('reason')}"
    parts = [
        f"亮度 {report['brightness']}",
        f"标题带 {report['title_band']}({'压暗' if report['dimmed'] else '未压暗'})",
        f"面板分 {report['panel_score']}",
        f"parse={report['parse']}",
    ]
    stall = report.get("stall") or {}
    if stall:
        # One shot has no history, so "静止 0.0s" here means "no earlier frame to compare
        # with" rather than "nothing has moved" — the sample count says which.
        parts.append(
            f"静止 {stall.get('static_s')}s 相似度 {stall.get('similarity')}"
            f" stalled={stall.get('stalled')} (样本 {stall.get('samples')}，1 时无历史)"
        )
    scrim_info = report.get("scrim") or {}
    if scrim_info.get("detected"):
        parts.append(
            f"有遮罩(比例 {scrim_info.get('ratio')}) 未压暗区 {scrim_info.get('undimmed_box_px')}"
        )
    return " | ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serial", required=True)
    parser.add_argument("--tap", nargs=2, type=int, metavar=("X", "Y"), help="tap once first")
    parser.add_argument("--wait", type=float, default=3.0, help="seconds after the tap")
    parser.add_argument("--save", help="keep this frame here")
    parser.add_argument("--json", action="store_true", help="the full report, not the summary")
    parser.add_argument("--fast", action="store_true", help="skip the accessibility dump (1-2 s)")
    args = parser.parse_args(argv)

    interpreter = wrong_interpreter()
    if interpreter:
        print(f"wrong interpreter: run me with {interpreter}", file=sys.stderr)
        return 2

    calibration = json.loads((SKILLS / SKILL / "calibration.json").read_text(encoding="utf-8"))
    if args.tap:
        print(f"tap ({args.tap[0]}, {args.tap[1]})", file=sys.stderr)
        tap(args.serial, *args.tap)
        time.sleep(max(0.0, args.wait))
    report = probe(args.serial, calibration, Path(args.save) if args.save else None, fast=args.fast)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else summarise(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
