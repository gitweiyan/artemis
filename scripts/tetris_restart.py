#!/usr/bin/env python3
"""End a game-over screen and start a new round: detect the panel, tap, verify.

The session's own end signal is the app's game-over panel, and a new round starts from its
重新开始 button. Both are pixels — a Unity surface has nothing in the accessibility tree,
and the repo's OCR is a cloud service this loop must not call — so both are read by
``skills/tetris/panel.py``:

* the panel: a committed patch of the title, correlated against the same rectangle of
  every frame. The measured gap is wide — 1.000 on two captures of the panel against
  0.051 on six frames of the game being played (``calibration.json`` → ``panel.measured``);
* the button: ``restart_flow.button_ratios``, the operator's reading of the live screen,
  checked in the tests against the white glyphs the frame actually shows at that spot.

How many taps the button needs is decided by the screen, not by this script: the panel is
looked for again after each tap, and tapping stops the moment it is gone. A blind second
tap would land in the middle of the new board.

"Panel gone" and "board readable" are reported separately: a restart that dismissed the
panel but left an ad or a dialog over the playfield has not started a round, and calling
that "restarted" would hide the problem behind the wrong word.

Usage:
    .venv/bin/python scripts/tetris_restart.py --serial EUWSOBXCZLPFNJF6
    .venv/bin/python scripts/tetris_restart.py --serial ... --dry-run   # look, do not tap
    .venv/bin/python scripts/tetris_restart.py --self-check             # no device at all

Output:
    a JSON report on stdout, and the frames it judged under ``captures/restart/<stamp>/``

Exit codes:
    0 the screen ends up showing a readable board (nothing to do, or the restart worked)
    2 wrong interpreter, or no serial given
    3 the panel is still there after the allowed taps
    4 the panel is gone but no board is readable — a different problem, reported as such
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SKILLS = PROJECT_ROOT / "skills"
SKILL = "tetris"
WAKE_TIMEOUT_S = 20
#: Counted so ``--self-check`` can *prove* it touched no device.
ADB_CALLS = {"count": 0}


def wrong_interpreter() -> str | None:
    """The project's dependencies live in ``.venv``; a bare ``python`` fails obscurely."""
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return None
    if Path(sys.executable).resolve() == venv_python.resolve():
        return None
    return str(venv_python)


def modules():
    """The skill's modules, loaded through the same registry the runtime uses."""
    from artemis.reflex.registry import load_skill as registry_load

    registry_load(SKILL, directory=SKILLS)
    return (
        importlib.import_module(f"artemis_skills_{SKILL}.panel"),
        importlib.import_module(f"artemis_skills_{SKILL}.perception"),
    )


def skill_calibration() -> dict:
    return json.loads((SKILLS / SKILL / "calibration.json").read_text(encoding="utf-8"))


def wake(serial: str) -> None:
    """A sleeping screen returns black frames, which read as nothing at all."""
    ADB_CALLS["count"] += 1
    subprocess.run(
        ["adb", "-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
        capture_output=True,
        timeout=WAKE_TIMEOUT_S,
        check=False,
    )


class Device:
    """The repository's own capture and control paths, so a tap here is a session's tap."""

    def __init__(self, serial: str):
        from artemis.reflex.capture import default_capture
        from artemis.reflex.control import AdbInputControl

        self.capture = default_capture(serial, prefer="screencap")
        self.control = AdbInputControl(serial)
        self.frames: list[tuple[str, bytes]] = []
        self.capture_ms: list[float] = []

    def look(self):
        started = time.monotonic()
        frame = self.capture.grab()
        self.capture_ms.append(round((time.monotonic() - started) * 1000, 1))
        self.frames.append((f"frame-{len(self.frames) + 1:02d}.png", getattr(frame, "image", b"")))
        return frame

    def tap(self, point: tuple[int, int]) -> float:
        from artemis.reflex.contract import Gesture

        ADB_CALLS["count"] += 1
        return self.control.dispatch(Gesture.tap(point[0], point[1], description="重新开始"))

    def save(self, directory: Path) -> list[str]:
        directory.mkdir(parents=True, exist_ok=True)
        for name, data in self.frames:
            if data:
                (directory / name).write_bytes(data)
        return [name for name, _ in self.frames]

    def close(self) -> None:
        self.capture.close()


def board_is_readable(frame, calibration: dict, perception) -> tuple[bool, str]:
    """Does this frame show the board again? Reported apart from "the panel is gone"."""
    state = perception.parse(frame, calibration)
    if state is None:
        return False, "no board in the frame (the identity check rejected it)"
    if state.get("game_over"):
        return False, "the frame still reads as the panel"
    matrix = state.get("board_matrix")
    if not matrix:
        return False, "no board matrix"
    return True, f"board {len(matrix[0])}x{len(matrix)}"


def run(serial: str, *, dry_run: bool, out_dir: Path, calibration: dict) -> dict:
    panel, perception = modules()
    point = panel.restart_point(calibration)
    if point is None:
        return {"ok": False, "reason": "no restart point in the calibration", "exit": 3}

    wake(serial)
    device = Device(serial)
    try:
        first = device.look()
        score = panel.score(first, calibration)
        detected = panel.is_game_over(first, calibration)
        threshold = calibration["panel"]["match_threshold"]
        print(
            f"panel score {score:.3f} (threshold {threshold}) → "
            f"{'游戏结束 is on screen' if detected else 'not the panel'}",
            file=sys.stderr,
        )
        result: dict = {"panel": detected, "score": round(score, 4), "taps": 0}
        if not detected:
            return {**result, "ok": True, "best_effort": "nothing to restart", "exit": 0}
        if dry_run:
            return {**result, "ok": True, "dry_run": True, "exit": 0}

        started = time.monotonic()
        report = panel.restart(device.look, device.tap, calibration)
        readable, why = board_is_readable(device.look(), calibration, perception)
    finally:
        device.close()

    exit_code = 0 if (report["restarted"] and readable) else (3 if not report["restarted"] else 4)
    return {
        "ok": exit_code == 0,
        "panel": True,
        "score": round(score, 4),
        "taps": report["taps"],
        "restart": report,
        "board": {"readable": readable, "detail": why},
        "seconds": round(time.monotonic() - started, 1),
        "capture_ms": device.capture_ms,
        "frames": device.save(out_dir),
        "directory": str(out_dir),
        "exit": exit_code,
    }


def self_check() -> int:
    """Every path except the device, on the frames committed in the repository."""
    panel, perception = modules()
    calibration = skill_calibration()
    samples = SKILLS / SKILL / "samples"
    failures: list[str] = []

    panel_frame = (samples / "panel-gameover.png").read_bytes()
    board_frame = (samples / "session-0001.png").read_bytes()
    checks = {
        "panel_detected_on_panel_frame": panel.is_game_over(panel_frame, calibration),
        "panel_detected_on_gameplay_frame": panel.is_game_over(board_frame, calibration),
    }
    if not checks["panel_detected_on_panel_frame"]:
        failures.append("the committed panel frame is not detected as the panel")
    if checks["panel_detected_on_gameplay_frame"]:
        failures.append("a gameplay frame is detected as the panel")

    looks = {"count": 0}

    def look() -> bytes:
        looks["count"] += 1
        return panel_frame if looks["count"] < 3 else board_frame

    taps: list[tuple[int, int]] = []
    report = panel.restart(look, taps.append, calibration, sleep=lambda _s: None)
    if not report["restarted"] or report["taps"] != 2 or len(taps) != 2:
        failures.append(f"the restart flow did not tap-until-gone: {report}")

    readable, detail = board_is_readable(board_frame, calibration, perception)
    if not readable:
        failures.append(f"a committed gameplay frame does not read back as a board: {detail}")

    if ADB_CALLS["count"]:
        failures.append(f"the self-check touched adb {ADB_CALLS['count']} times")

    print(
        json.dumps(
            {
                **checks,
                "restart_taps": report["taps"],
                "restart_point": list(panel.restart_point(calibration) or ()),
                "board_readable": readable,
                "adb_calls": ADB_CALLS["count"],
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serial", help="device serial (adb -s)")
    parser.add_argument("--dry-run", action="store_true", help="detect and report, never tap")
    parser.add_argument("--self-check", action="store_true", help="no device: offline paths only")
    parser.add_argument("--out", help="where to keep the judged frames")
    args = parser.parse_args(argv)

    interpreter = wrong_interpreter()
    if interpreter:
        print(
            f"wrong interpreter: run me with {interpreter}\n"
            f"  {interpreter} scripts/tetris_restart.py --serial <serial>",
            file=sys.stderr,
        )
        return 2
    if args.self_check:
        return self_check()
    if not args.serial:
        print("--serial is required (or --self-check)", file=sys.stderr)
        return 2

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else PROJECT_ROOT / "captures" / "restart" / stamp
    result = run(
        args.serial, dry_run=args.dry_run, out_dir=out_dir, calibration=skill_calibration()
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result.get("exit", 1))


if __name__ == "__main__":
    sys.exit(main())
