#!/usr/bin/env python3
"""Play a real round on the device: start it, drive the inner loop, report what happened.

This is the whole loop in one command — the outer loop's part in miniature (start a round)
and the inner loop as it will run for real:

1. **start a round** — if the game-over panel is up, press the two calibrated buttons
   (`panel.restart`) and check that a board is readable before playing;
2. **play** — the reflex runtime drives the skill: capture → ``perception.parse`` → Jev
   decides the rotation and then the column (``policy_jev``) → taps through the game's own
   four buttons. No hard drop (the piece falls on its own, which leaves frames to correct);
3. **report** — the runtime's metrics, the skill's counters, the Jev call telemetry, and a
   per-frame trace under ``captures/play/<stamp>/trace.jsonl``: the board matrix, the piece,
   the decision and every gesture. The frames themselves are kept every ``--frames-every``
   frames so a human can check the trace against what the screen showed.

Correctness is the operator's to judge: this writes down what happened, it does not decide
whether the play was good.

Usage:
    .venv/bin/python scripts/tetris_play.py --serial EUWSOBXCZLPFNJF6 --seconds 30
    .venv/bin/python scripts/tetris_play.py --serial ... --fresh        # restart first
    .venv/bin/python scripts/tetris_play.py --serial ... --no-frames    # trace only

Exit codes:
    0 the session ran (time budget reached, or the skill ended it: panel or still board)
    2 wrong interpreter / no serial
    3 the session was lost (blind frames past the threshold, or a skill error)
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SKILLS = PROJECT_ROOT / "skills"
SKILL = "tetris"


def wrong_interpreter() -> str | None:
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return None
    if Path(sys.executable).resolve() == venv_python.resolve():
        return None
    return str(venv_python)


class Trace:
    """The session's own record: one JSON line per frame, plus a shared clock."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("w", encoding="utf-8")
        self.started = time.monotonic()
        self.record: dict = {}
        self.frames = 0

    def t(self) -> float:
        return round(time.monotonic() - self.started, 3)

    def begin(self, frame) -> None:
        """Start a new line for this frame: what the camera saw, before any decision."""
        self.flush()
        self.frames += 1
        self.record = {
            "frame": self.frames,
            "t": self.t(),
            "frame_ms": round(float(getattr(frame, "capture_ms", 0.0)), 1),
            "width": getattr(frame, "width", None),
            "height": getattr(frame, "height", None),
        }

    def update(self, **fields) -> None:
        self.record.update(fields)

    def gesture(self, gesture, ms: float) -> None:
        self.record.setdefault("gestures", []).append(
            {
                "kind": gesture.kind,
                "points": [list(point) for point in gesture.points],
                "description": gesture.description,
                "ms": round(float(ms), 1),
            }
        )

    def flush(self) -> None:
        if self.record:
            self.handle.write(json.dumps(self.record, ensure_ascii=False) + "\n")
            self.handle.flush()
            self.record = {}

    def close(self) -> None:
        self.flush()
        self.handle.close()


class RecordingCapture:
    """A capture that keeps what the loop saw — every ``every``-th frame on disk."""

    def __init__(self, inner, trace: Trace, directory: Path, every: int):
        self.inner = inner
        self.trace = trace
        self.directory = directory
        self.every = max(1, int(every))
        self.index = 0
        if self.every:
            self.directory.mkdir(parents=True, exist_ok=True)

    def grab(self):
        frame = self.inner.grab()
        self.index += 1
        self.trace.begin(frame)
        if self.every and self.index % self.every == 0:
            (self.directory / f"frame-{self.index:04d}.png").write_bytes(frame.image)
        return frame

    def close(self) -> None:
        self.inner.close()


class LoggingControl:
    """A control that writes each gesture into the frame's trace line."""

    def __init__(self, inner, trace: Trace):
        self.inner = inner
        self.trace = trace

    def dispatch(self, gesture) -> float:
        started = time.monotonic()
        ms = self.inner.dispatch(gesture)
        self.trace.gesture(gesture, ms if ms else (time.monotonic() - started) * 1000)
        points = ", ".join(f"({int(x)},{int(y)})" for x, y in gesture.points)
        print(f"  {self.trace.t():7.2f}s → {gesture.kind} {points} ({ms:.0f} ms)", flush=True)
        return ms


def traced_skill(handle, trace: Trace, perception: dict):
    """Wrap the skill's two entry points so the trace says what each frame decided."""

    def parse(frame):
        state = handle.parse(frame)
        if isinstance(state, dict):
            matrix = state.get("board_matrix") or []
            perception["filled"] = sum(row.count("1") for row in matrix)
            perception["piece_cells"] = len(state.get("current_piece_positions") or [])
            perception["matrix"] = matrix
            perception["stall"] = state.get("stall")
            perception["piece_mask"] = state.get("current_piece_mask")
            perception["game_over"] = bool(state.get("game_over"))
            trace.update(
                kind="board" if matrix else ("panel" if state.get("game_over") else "blank"),
                filled=perception["filled"],
                piece_cells=perception["piece_cells"],
                piece_mask=state.get("current_piece_mask"),
                piece_positions=state.get("current_piece_positions"),
                matrix=matrix,
                stall=state.get("stall"),
            )
        else:
            trace.update(kind="not a board")
        return state

    def decide(state, calibration):
        action = handle.decide(state, calibration)
        detail = getattr(action, "state", None) or {}
        trace.update(
            action={
                "done": bool(action.done),
                "phase": detail.get("phase"),
                "rotate_taps": detail.get("rotate_taps"),
                "move_columns": detail.get("move_columns"),
                "on_target": detail.get("on_target"),
                "note": detail.get("note"),
                "placement": detail.get("placement"),
                "source": detail.get("source") or (detail.get("counters") or {}).get("source"),
                # Whether this frame reused the previous decision: without it a trace cannot
                # tell "one Jev call per piece" from "one per frame".
                "reused": (detail.get("counters") or {}).get("reused"),
            }
        )
        if action.done:
            print(
                f"  {trace.t():7.2f}s ■ session ended by the skill: {detail.get('note')}",
                flush=True,
            )
        elif detail.get("phase"):
            print(
                f"  {trace.t():7.2f}s · filled {perception.get('filled')} "
                f"piece {perception.get('piece_cells')} → {detail.get('phase')} "
                f"taps={detail.get('rotate_taps')} cols={detail.get('move_columns')} "
                f"col={detail.get('current_column')}",
                flush=True,
            )
        return action

    return replace(handle, parse=parse, decide=decide)


def start_a_round(panel, perception_mod, calibration, device, *, fresh: bool) -> dict:
    """Press the restart buttons if the panel is up, then check a board is readable."""
    frame = device.grab()
    panel_up = panel.is_game_over(frame.image, calibration)
    if not panel_up and not fresh:
        return {"panel_was_up": False, "restarted": False, "reason": "a round was already on"}
    print(f"panel score {panel.score(frame.image, calibration):.3f} → starting a new round")
    report = panel.restart(lambda: device.grab(), lambda point: device.tap(point), calibration)
    print(
        f"  restart: {report['taps']} taps, started at step {report['started_at_step']}, {report['reason']}"
    )
    for _ in range(6):
        time.sleep(1.0)
        frame = device.grab()
        state = perception_mod.parse(frame, calibration)
        if state is not None and not state.get("game_over") and state.get("board_matrix"):
            return {"panel_was_up": bool(panel_up), "restarted": True, "restart": report}
    return {
        "panel_was_up": bool(panel_up),
        "restarted": False,
        "restart": report,
        "reason": "no readable board after the restart",
    }


def progressing(perception_mod, calibration, device, *, seconds: float = 5.0) -> dict:
    """Is the round actually moving? A piece falling, or the board changing at all.

    Measured on the device: a *stuck* game keeps drawing its play screen (the control row
    and HUD match a playing frame band for band) while nothing moves except the ad banner —
    Unity paused, most likely by the ad SDK's activity coming and going. A tap on the
    playfield does not wake it; relaunching the app does.
    """
    frames = max(2, int(seconds / 1.5))
    seen_piece = False
    matrices = []
    for _ in range(frames):
        state = perception_mod.parse(device.grab(), calibration)
        if state is not None and state.get("board_matrix"):
            matrices.append(state["board_matrix"])
            if state.get("current_piece_positions"):
                seen_piece = True
        time.sleep(1.5)
    changed = any(m != matrices[0] for m in matrices[1:])
    return {
        "moving": bool(seen_piece or changed),
        "piece_seen": seen_piece,
        "board_changed": changed,
    }


def relaunch(serial: str) -> bool:
    """Bring the game back: HOME, then its launcher activity, then wait for a frame.

    ``am start -n .../MainActivity`` did *not* work (it left the home screen up); the
    package's own launcher activity is ``cn.flytalk.game.SplashActivity``, which
    ``monkey`` reaches. Measured: the round is still there afterwards (Unity resumes, the
    process was never killed).
    """
    package = "cn.flytalk.tetris.nearme.gamecenter"
    for command in (
        ["shell", "input", "keyevent", "KEYCODE_HOME"],
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
    ):
        result = subprocess.run(
            ["adb", "-s", serial, *command], capture_output=True, timeout=30, check=False
        )
        if result.returncode != 0:
            return False
        time.sleep(2.0)
    time.sleep(8.0)
    return True


def ensure_playable(panel, perception_mod, calibration, device, serial, *, fresh: bool) -> dict:
    """Get to a screen that is worth playing on, and say what it took."""
    frame = device.grab()
    if panel.is_game_over(frame.image, calibration):
        report = start_a_round(panel, perception_mod, calibration, device, fresh=True)
        if report.get("restarted"):
            return {"route": "restart", **report}
        # Measured: a frozen game shows the panel and ignores the buttons (both presses left
        # the panel at the same brightness instead of dimming it), so the round is skipped
        # unless the app is woken first. Relaunch, then try the buttons again.
        print("  the restart did not take (a frozen game ignores its buttons) → relaunching")
        if not relaunch(serial):
            return {"route": "restart-failed", **report}
        frame = device.grab()
        if panel.is_game_over(frame.image, calibration):
            retry = start_a_round(panel, perception_mod, calibration, device, fresh=True)
            return {"route": "relaunch+restart", "first_attempt": report, **retry}
        state = perception_mod.parse(frame, calibration)
        if state is not None and state.get("board_matrix"):
            return {"route": "relaunch", "relaunched": True, "first_attempt": report}
        return {"route": "restart-failed", **report}
    state = perception_mod.parse(frame, calibration)
    if state is None or not state.get("board_matrix"):
        return {"route": "none", "reason": "no readable board on screen"}
    check = progressing(perception_mod, calibration, device)
    if check["moving"]:
        return {"route": "already playing", **check}
    print("  the board is not moving (no piece, no change) → relaunching the app")
    if not relaunch(serial):
        return {"route": "none", "reason": "could not relaunch the game", **check}
    frame = device.grab()
    if panel.is_game_over(frame.image, calibration):
        report = start_a_round(panel, perception_mod, calibration, device, fresh=True)
        return {"route": "relaunch+restart", **report}
    state = perception_mod.parse(frame, calibration)
    if state is not None and state.get("board_matrix"):
        return {"route": "relaunch", "relaunched": True, **check}
    return {"route": "none", "reason": "still nothing playable after the relaunch", **check}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serial", required=True)
    parser.add_argument("--seconds", type=float, default=60.0, help="total play budget")
    parser.add_argument(
        "--session-seconds", type=float, default=60.0, help="budget for one reflex session"
    )
    parser.add_argument("--rounds", type=int, default=1, help="how many sessions to run")
    parser.add_argument("--fresh", action="store_true", help="restart even if a round is on")
    parser.add_argument("--fps-cap", type=float, default=3.0)
    parser.add_argument("--frames-every", type=int, default=5, help="0 disables frame saving")
    parser.add_argument("--no-frames", action="store_true")
    parser.add_argument("--out", help="where the trace and frames go")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    interpreter = wrong_interpreter()
    if interpreter:
        print(f"wrong interpreter: run me with {interpreter}", file=sys.stderr)
        return 2

    from artemis.reflex.capture import default_capture
    from artemis.reflex.control import AdbInputControl
    from artemis.reflex.registry import load_skill
    from artemis.reflex.runtime import run_reflex

    load_skill(SKILL, directory=SKILLS)
    panel = importlib.import_module(f"artemis_skills_{SKILL}.panel")
    perception_mod = importlib.import_module(f"artemis_skills_{SKILL}.perception")
    policy_jev = importlib.import_module(f"artemis_skills_{SKILL}.policy_jev")
    calibration = json.loads((SKILLS / SKILL / "calibration.json").read_text(encoding="utf-8"))

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else PROJECT_ROOT / "captures" / "play" / stamp
    trace = Trace(out / "trace.jsonl")
    device = default_capture(args.serial, prefer="screencap")
    actuator = AdbInputControl(args.serial)

    try:
        if hasattr(policy_jev, "reset_telemetry"):
            policy_jev.reset_telemetry()
        print(
            f"== 开始（总预算 {args.seconds:.0f}s，每局 {args.session_seconds:.0f}s，"
            f"最多 {args.rounds} 局，fps_cap {args.fps_cap}）==",
            flush=True,
        )
        perception: dict = {}
        handle = traced_skill(load_skill(SKILL, directory=SKILLS), trace, perception)
        rounds: list[dict] = []
        spent = 0.0
        for index in range(max(1, args.rounds)):
            if spent >= args.seconds:
                break
            route = ensure_playable(
                panel,
                perception_mod,
                calibration,
                _Starter(device, actuator),
                args.serial,
                fresh=args.fresh,
            )
            print(f"-- 第 {index + 1} 局前置: {route}", flush=True)
            if route["route"] == "none":
                rounds.append({"round": index + 1, "skipped": route})
                break
            budget = max(5.0, min(args.session_seconds, args.seconds - spent))
            print(f"== 内环运行中（本局最多 {budget:.0f}s）==", flush=True)
            capture = RecordingCapture(
                device, trace, out / "frames", 0 if args.no_frames else args.frames_every
            )
            result = run_reflex(
                handle,
                capture=capture,
                control=LoggingControl(actuator, trace),
                seconds=budget,
                fps_cap=args.fps_cap,
            )
            spent += result.seconds
            # The counters are reset when a session starts, so a multi-round run has to read
            # them per round: reading once at the end reports only the last one.
            round_telemetry = policy_jev.telemetry() if hasattr(policy_jev, "telemetry") else {}
            note = (
                (result.final_state or {}).get("note")
                if isinstance(result.final_state, dict)
                else None
            )
            entry = {
                "round": index + 1,
                "stop_reason": str(getattr(result.stop_reason, "value", result.stop_reason)),
                "seconds": round(result.seconds, 1),
                "frames": result.frames,
                "actions": result.actions,
                "fps": round(result.fps, 2),
                "capture_ms_p50": result.capture_ms_p50,
                "capture_ms_p95": result.capture_ms_p95,
                "blind_frames": result.blind_frames,
                "control_failures": result.control_failures,
                "skill_error": result.skill_error,
                "counters": result.counters,
                "final_note": note,
                "route": route,
                "jev": round_telemetry,
            }
            rounds.append(entry)
            print(
                f"   → {entry['stop_reason']} {entry['seconds']}s 帧 {entry['frames']} "
                f"动作 {entry['actions']} fps {entry['fps']} note={note}",
                flush=True,
            )
            if entry["stop_reason"] == "time_budget":
                break
    finally:
        trace.close()
        device.close()

    telemetry = policy_jev.telemetry() if hasattr(policy_jev, "telemetry") else {}
    totals = {
        "jev": {
            key: sum(int(entry.get("jev", {}).get(key) or 0) for entry in rounds)
            for key in ("calls", "fallbacks", "reuses")
        },
        "rounds_played": len(rounds),
        "seconds": round(spent, 1),
        "frames": sum(entry.get("frames", 0) for entry in rounds),
        "actions": sum(entry.get("actions", 0) for entry in rounds),
        "blind_frames": sum(entry.get("blind_frames", 0) for entry in rounds),
        "control_failures": sum(entry.get("control_failures", 0) for entry in rounds),
        "skill_error": next((e["skill_error"] for e in rounds if e.get("skill_error")), None),
        "stop_reason": rounds[-1].get("stop_reason") if rounds else None,
        "final_note": rounds[-1].get("final_note") if rounds else None,
    }
    report = {
        **totals,
        "jev": totals["jev"],
        "jev_last_session": telemetry,
        "per_round": rounds,
        "directory": str(out),
        "trace": str(trace.path),
    }
    print("\n== 会话结果 ==", flush=True)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for key in (
            "rounds_played",
            "seconds",
            "frames",
            "actions",
            "blind_frames",
            "skill_error",
            "jev",
        ):
            print(f"  {key}: {report[key]}")
        for entry in rounds:
            print(
                f"  第{entry['round']}局: {entry.get('stop_reason')} {entry.get('seconds')}s "
                f"动作 {entry.get('actions')} note={entry.get('final_note')} route={(entry.get('route') or {}).get('route')}"
            )
        print(f"  trace: {report['trace']}")
    lost = any(entry.get("stop_reason") == "lost" or entry.get("skill_error") for entry in rounds)
    return 3 if lost else 0


class _Starter:
    """The two gestures starting a round needs, without building a whole capture stack."""

    def __init__(self, capture, actuator):
        self.capture = capture
        self.actuator = actuator

    def grab(self):
        return self.capture.grab()

    def tap(self, point: tuple[int, int]) -> float:
        from artemis.reflex.contract import Gesture

        return self.actuator.dispatch(Gesture.tap(point[0], point[1], description="重新开始"))


if __name__ == "__main__":
    sys.exit(main())
