#!/usr/bin/env python3
"""Capture a series of frames from a device while a game plays itself — passive.

For testing *falling-piece identification* the frames are not enough on their own: the
test needs to know what actually happened, so this script records three things at once:

* the frames (lossless PNG, one `adb exec-out screencap -p` each) — the pixels the CV
  pipeline reads;
* the timing that actually happened (`t_mono`, `capture_ms` measured per frame, not the
  requested interval: `screencap` takes ~330 ms on PEMM00 and the real interval drifts);
* a concurrent `screenrecord` video — the ground truth a human can watch, because a
  still frame cannot show whether a piece is falling or already locked.

It sends **no input** to the game beyond one wake-up at the start (a sleeping screen
returns black frames): no taps, no swipes, nothing that would change the position being
observed. It also refuses to pretend it captured anything useful — a frame that is not
this board, a black frame, or a board that never changes is counted and reported.

Usage:
    .venv/bin/python scripts/capture_frame_series.py --serial EUWSOBXCZLPFNJF6 \\
        --seconds 10 --interval 0.4

    # every path except the device, on the frames already in this repository:
    .venv/bin/python scripts/capture_frame_series.py --self-check

Output:
    captures/<utc-stamp>-passive/
        frames/frame-0001.png ...
        manifest.jsonl            one line per frame, including the per-frame analysis
        groundtruth/gt-000.mp4    screenrecord segments (if --video)
        summary.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: The device has hung on single adb commands before (83 s observed), so every call is
#: bounded rather than left to the transport.
SHELL_TIMEOUT_S = 20
SCREENCAP_TIMEOUT_S = 30
#: `screenrecord --time-limit` is capped on most devices; segments are chained.
VIDEO_SEGMENT_S = 170
BRIGHT_ENOUGH = 10.0
#: Counted so ``--self-check`` can *prove* it touched no device.
ADB_CALLS = {"count": 0}


def wrong_interpreter() -> str | None:
    """The project's dependencies (pydantic, numpy, PIL) live in ``.venv``.

    A bare ``python`` — a system or conda interpreter — fails deep inside an import with
    a traceback that says nothing about the real problem. Name the right interpreter
    instead, before importing anything from the project.
    """
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return None
    if Path(sys.executable).resolve() == venv_python.resolve():
        return None
    return str(venv_python)


def adb(serial: str, *args: str, timeout: float = SHELL_TIMEOUT_S, binary: bool = False):
    """One bounded adb call. Returns ``(returncode, stdout, stderr)``."""
    ADB_CALLS["count"] += 1
    completed = subprocess.run(
        ["adb", "-s", serial, *args],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if binary:
        return completed.returncode, completed.stdout, completed.stderr
    return (
        completed.returncode,
        completed.stdout.decode("utf-8", "replace"),
        completed.stderr.decode("utf-8", "replace"),
    )


class VideoSegments:
    """`screenrecord` in segments: the device caps one recording's length."""

    def __init__(self, serial: str, directory: Path, enabled: bool):
        self.serial = serial
        self.directory = directory
        self.enabled = enabled
        self.process: subprocess.Popen | None = None
        self.index = 0
        self.files: list[str] = []
        self.problem: str | None = None
        #: When the first segment started, as an offset from the capture start — so a
        #: labelling pass can align the frames with the video instead of guessing.
        self.first_start_t_mono: float | None = None

    def _remote(self) -> str:
        return f"/sdcard/artemis-frames-gt-{self.index:03d}.mp4"

    def ensure_running(self, seconds_left: float, t_mono: float) -> None:
        if not self.enabled or self.process is not None:
            return
        limit = int(max(5, min(VIDEO_SEGMENT_S, seconds_left + 5)))
        try:
            self.process = subprocess.Popen(
                [
                    "adb",
                    "-s",
                    self.serial,
                    "shell",
                    "screenrecord",
                    "--time-limit",
                    str(limit),
                    "--bit-rate",
                    "8000000",
                    self._remote(),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:  # pragma: no cover - adb missing is reported, not raised
            self.enabled = False
            self.problem = str(exc)
            return
        ADB_CALLS["count"] += 1  # the adb shell screenrecord above
        if self.first_start_t_mono is None:
            self.first_start_t_mono = round(t_mono, 4)

    def _finish_segment(self) -> None:
        remote = self._remote()
        if self.process is not None:
            try:
                # SIGINT makes screenrecord finalise the file rather than leave a stub.
                adb(self.serial, "shell", "pkill", "-INT", "screenrecord")
                self.process.wait(timeout=15)
            except (subprocess.TimeoutExpired, OSError):
                self.process.terminate()
            self.process = None
        self.directory.mkdir(parents=True, exist_ok=True)
        local = self.directory / f"gt-{self.index:03d}.mp4"
        code, _out, err = adb(self.serial, "pull", remote, str(local))
        if code == 0 and local.exists() and local.stat().st_size > 0:
            self.files.append(str(local.relative_to(PROJECT_ROOT)))
        elif self.problem is None:
            self.problem = err.strip()[:200] or "screenrecord produced no file"
        adb(self.serial, "shell", "rm", "-f", remote)
        self.index += 1

    def rotate_if_done(self, seconds_left: float, t_mono: float) -> None:
        """Chain the next segment when the running one has finished on its own."""
        if self.process is not None and self.process.poll() is not None:
            self._finish_segment()
        self.ensure_running(seconds_left, t_mono)

    def stop(self) -> None:
        if self.process is not None:
            self._finish_segment()


def analyse(png: bytes, modules: dict, calibration: dict) -> dict:
    """What the CV pipeline sees in this frame — recorded per frame, never asserted on."""
    import numpy as np
    from PIL import Image

    array = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype="float32")
    result: dict = {"mean_luma": round(float(array.mean()), 2)}
    field = modules["roi"].board_crop(array, calibration)
    probe = modules["grid_probe"].measure_grid(field, calibration)
    problem = modules["grid_probe"].identity_problem(
        probe, modules["grid_probe"].declared_cells(calibration)
    )
    result.update(
        {
            "columns": probe["columns"],
            "rows": probe["rows"],
            "pitch_x": probe["pitch_x"],
            "pitch_y": probe["pitch_y"],
            "extent_ok": probe["extent_ok"],
            "board_problem": problem,
        }
    )
    if problem is None:
        result["matrix"] = modules["cells"].read_matrix(field, calibration)
        result["filled"] = sum(row.count("1") for row in result["matrix"])
    return result


def frame_record(
    index: int,
    png: bytes,
    *,
    modules: dict,
    calibration: dict,
    previous_matrix: list[str] | None,
    t_mono: float,
    capture_ms: float | None = None,
    adb_rc: int | None = None,
    write_frame: Path | None = None,
) -> tuple[dict, list[str] | None]:
    """One frame → its manifest record, including what the CV pipeline makes of it.

    Shared by the device path and ``--self-check``, so the check exercises the real
    analysis, the real pair comparison and the real piece split — not a lookalike.
    """
    record: dict = {
        "index": index,
        "t_mono": round(t_mono, 4),
        "t_wall": datetime.now(UTC).isoformat(timespec="milliseconds"),
    }
    if capture_ms is not None:
        record["capture_ms"] = round(capture_ms, 1)
    if adb_rc is not None:
        record["adb_rc"] = adb_rc
    if write_frame is not None:
        write_frame.parent.mkdir(parents=True, exist_ok=True)
        write_frame.write_bytes(png)
        record.update(
            {
                "file": str(write_frame.relative_to(PROJECT_ROOT)),
                "bytes": len(png),
                "sha256": hashlib.sha256(png).hexdigest()[:16],
            }
        )
    try:
        analysis = analyse(png, modules, calibration)
    except Exception as exc:  # noqa: BLE001 - an analysis failure is data, not a crash
        analysis = {"analysis_error": f"{type(exc).__name__}: {exc}"}
    record.update(analysis)

    matrix = analysis.get("matrix")
    if previous_matrix is not None and matrix:
        same_shape = len(previous_matrix) == len(matrix) and len(previous_matrix[0]) == len(
            matrix[0]
        )
        if same_shape:
            record["appeared"] = sum(
                1
                for prev, now in zip(previous_matrix, matrix, strict=True)
                for a, b in zip(prev, now, strict=True)
                if a == "0" and b == "1"
            )
            record["vanished"] = sum(
                1
                for prev, now in zip(previous_matrix, matrix, strict=True)
                for a, b in zip(prev, now, strict=True)
                if a == "1" and b == "0"
            )
        else:
            # The grid measurement changed shape between frames. `pieces` reports no
            # piece for such a pair, and a cell-by-cell count would be meaningless.
            record["grid_shape_changed"] = True
        split = modules["pieces"].split_pieces([previous_matrix, matrix])
        record["piece_mask"] = split["current_piece_mask"]
        record["motion_vector"] = split["motion_vector"]
        record["piece_cells"] = len(split["current_piece_positions"])
    return record, (matrix or previous_matrix)


def status_line(record: dict) -> str:
    lines = f"#{record['index']:04d} t=+{record['t_mono']:6.2f}s"
    if "capture_ms" in record:
        lines += f" cap={record['capture_ms']:5.0f}ms"
    luma = record.get("mean_luma")
    lines += f" lum={luma:5.1f}" if isinstance(luma, int | float) else " lum=  n/a"
    if record.get("analysis_error"):
        return f"{lines} ANALYSIS FAILED: {record['analysis_error']}"
    if record.get("board_problem"):
        return f"{lines} NOT A BOARD: {record['board_problem']}"
    lines += f" filled={record.get('filled')}"
    if "appeared" in record:
        lines += f" Δ+{record['appeared']}/-{record['vanished']}"
    elif record.get("grid_shape_changed"):
        lines += " grid shape changed"
    if record.get("piece_cells"):
        lines += f" piece={record['piece_mask']} motion={record['motion_vector']}"
    else:
        lines += " piece=none"
    return lines


def output_root(args: argparse.Namespace) -> Path:
    """Where the session goes, resolved against the repository.

    Relative `--out` must resolve the same way whether the script is run from the
    repository root or elsewhere, and the `file` written into the manifest has to be
    relative to the repository — a relative session path is not.
    """
    root = Path(args.out)
    return root if root.is_absolute() else PROJECT_ROOT / root


def _modules() -> tuple[dict, dict]:
    from artemis.reflex.registry import load_skill

    calibration = load_skill("tetris", directory=PROJECT_ROOT / "skills").calibration
    modules = {
        name: importlib.import_module(f"artemis_skills_tetris.{name}")
        for name in ("roi", "grid_probe", "cells", "pieces")
    }
    return modules, calibration


def self_check(args: argparse.Namespace) -> int:
    """Every path except the device, on the frames already committed to this repository."""
    modules, calibration = _modules()
    session = output_root(args) / "self-check"
    frames_dir = session / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    samples = sorted((PROJECT_ROOT / "skills" / "tetris" / "samples").glob("reference-*"))
    if not samples:
        print("self-check needs the reference frames in skills/tetris/samples/", file=sys.stderr)
        return 2
    video = VideoSegments(args.serial, session / "groundtruth", args.video)
    print(
        "self-check: no adb call is made. resolved options: "
        + json.dumps(
            {
                "serial": args.serial,
                "seconds": args.seconds,
                "interval": args.interval,
                "video": video.enabled,
                "out": args.out,
                "allow_no_board": args.allow_no_board,
            },
            ensure_ascii=False,
        )
    )
    records: list[dict] = []
    previous: list[str] | None = None
    with (session / "manifest.jsonl").open("w", encoding="utf-8") as manifest:
        for index, path in enumerate(samples, start=1):
            record, previous = frame_record(
                index,
                path.read_bytes(),
                modules=modules,
                calibration=calibration,
                previous_matrix=previous,
                t_mono=(index - 1) * args.interval,
                write_frame=frames_dir / f"frame-{index:04d}.png",
            )
            manifest.write(json.dumps(record) + "\n")
            records.append(record)
            print("  " + status_line(record))
    summary = _summary(records, args, session, video, aborted=None)
    summary["adb_calls"] = ADB_CALLS["count"]
    (session / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nself-check session: {session}")
    if ADB_CALLS["count"]:
        print(f"FAIL: self-check made {ADB_CALLS['count']} adb call(s); it must make none.")
        return 1
    print("OK: analysis, pair comparison, piece split and summary all ran, with 0 adb calls.")
    return 0


def capture(args: argparse.Namespace) -> int:
    """The device path: passive frames, plus a concurrent recording for ground truth."""
    modules, calibration = _modules()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    session = output_root(args) / f"{stamp}-passive"
    frames_dir = session / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = session / "manifest.jsonl"

    code, out, _err = adb(args.serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    print(f"wake-up: rc={code} {out.strip()}")

    video = VideoSegments(args.serial, session / "groundtruth", args.video)
    deadline = time.monotonic() + max(1.0, args.seconds)
    started = time.monotonic()
    records: list[dict] = []
    previous: list[str] | None = None
    first_frame_checked = False
    aborted: str | None = None
    index = 0

    with manifest_path.open("w", encoding="utf-8") as manifest:
        while time.monotonic() < deadline:
            slot = time.monotonic()
            index += 1
            if first_frame_checked:
                # Started only after the first frame proved this is the board, so a
                # refused run does not leave a one-frame video behind.
                video.rotate_if_done(deadline - slot, slot - started)
            capture_started = time.monotonic()
            rc, png, err = adb(
                args.serial, "exec-out", "screencap", "-p", timeout=SCREENCAP_TIMEOUT_S, binary=True
            )
            capture_ms = (time.monotonic() - capture_started) * 1000.0
            if rc != 0 or not png.startswith(PNG_MAGIC):
                record = {
                    "index": index,
                    "t_mono": round(slot - started, 4),
                    "t_wall": datetime.now(UTC).isoformat(timespec="milliseconds"),
                    "capture_ms": round(capture_ms, 1),
                    "adb_rc": rc,
                    "error": (err or b"")[:200].decode("utf-8", "replace") or "no PNG",
                }
                manifest.write(json.dumps(record) + "\n")
                manifest.flush()
                records.append(record)
                print(f"#{index:04d} CAPTURE FAILED rc={rc} {record['error']}")
                first_frame_checked = True
                continue

            record, previous = frame_record(
                index,
                png,
                modules=modules,
                calibration=calibration,
                previous_matrix=previous,
                t_mono=slot - started,
                capture_ms=capture_ms,
                adb_rc=rc,
                write_frame=frames_dir / f"frame-{index:04d}.png",
            )
            if (
                isinstance(record.get("mean_luma"), int | float)
                and record["mean_luma"] < BRIGHT_ENOUGH
            ):
                record["black_frame"] = True
                adb(args.serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
                print(
                    f"#{index:04d} luma={record['mean_luma']:.1f} -> BLACK, woke the screen again"
                )

            manifest.write(json.dumps(record) + "\n")
            manifest.flush()
            records.append(record)
            print(status_line(record))

            if not first_frame_checked:
                first_frame_checked = True
                if record.get("board_problem") and not args.allow_no_board:
                    aborted = (
                        f"the first frame is not this board ({record['board_problem']}):"
                        " is the game on screen and playing? --allow-no-board captures anyway."
                    )
                    break

            sleep_for = args.interval - (time.monotonic() - slot)
            if sleep_for > 0:
                time.sleep(sleep_for)

    video.stop()
    summary = _summary(records, args, session, video, aborted=aborted)
    (session / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nsession: {session}")
    if aborted:
        print(f"aborted: {aborted}", file=sys.stderr)
        return 3
    return 0


def _summary(
    records: list[dict],
    args: argparse.Namespace,
    session: Path,
    video: VideoSegments,
    aborted: str | None,
) -> dict:
    captured = [r for r in records if "file" in r]
    intervals = [
        round(b["t_mono"] - a["t_mono"], 4)
        for a, b in zip(records, records[1:], strict=False)
        if "t_mono" in a and "t_mono" in b
    ]
    pairs = [r for r in records if "appeared" in r or r.get("grid_shape_changed")]
    moved = [r for r in pairs if (r.get("appeared") or 0) + (r.get("vanished") or 0) > 0]
    identified = [r for r in pairs if r.get("piece_cells")]
    phantom_suspects = [
        r
        for r in pairs
        if not r.get("piece_cells") and 0 < (r.get("appeared") or 0) + (r.get("vanished") or 0) <= 2
    ]
    fall = sorted(
        r["motion_vector"][0]
        for r in identified
        if r.get("motion_vector") and r["motion_vector"][0]
    )
    median_interval = sorted(intervals)[len(intervals) // 2] if intervals else None
    capture_times = sorted(r["capture_ms"] for r in captured if "capture_ms" in r)
    return {
        "aborted": aborted,
        "serial": args.serial,
        "requested_interval_s": args.interval,
        "requested_seconds": args.seconds,
        "frames_attempted": len(records),
        "frames_captured": len(captured),
        "capture_failures": len(records) - len(captured),
        "black_frames": sum(1 for r in captured if r.get("black_frame")),
        "not_this_board": sum(1 for r in captured if r.get("board_problem")),
        "real_interval_s_p50": median_interval,
        "real_interval_s_max": max(intervals) if intervals else None,
        "capture_ms_p50": capture_times[len(capture_times) // 2] if capture_times else None,
        "pairs_compared": len(pairs),
        "pairs_where_the_board_changed": len(moved),
        "pairs_with_a_piece_identified": len(identified),
        "pairs_with_a_1_or_2_cell_change_no_piece": len(phantom_suspects),
        "fall_rows_per_frame_p50": fall[len(fall) // 2] if fall else None,
        "fall_cells_per_second_p50": round(fall[len(fall) // 2] / median_interval, 2)
        if fall and median_interval
        else None,
        "video": video.files or None,
        "video_problem": video.problem,
        "video_first_start_t_mono": video.first_start_t_mono,
        "session": str(session),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default="", help="Device serial (adb -s).")
    parser.add_argument("--seconds", type=float, default=10.0, help="Capture duration.")
    parser.add_argument(
        "--interval",
        type=float,
        default=0.4,
        help="Start-to-start target interval. The measured one is recorded per frame.",
    )
    parser.add_argument("--out", default="captures", help="Directory for the session.")
    parser.add_argument(
        "--video",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Concurrent screenrecord for ground truth (default: on).",
    )
    parser.add_argument(
        "--allow-no-board",
        action="store_true",
        help="Keep going when the first frame is not this board (default: refuse).",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Run every path except the device, on this repository's reference frames.",
    )
    args = parser.parse_args()

    interpreter = wrong_interpreter()
    if interpreter:
        script = Path(__file__).resolve().relative_to(PROJECT_ROOT)
        print(
            "wrong interpreter: this project's dependencies are in .venv, not in "
            f"{sys.executable}\nrun it as:\n  {interpreter} {script} --serial <SERIAL> --seconds 10",
            file=sys.stderr,
        )
        return 2
    if args.self_check:
        return self_check(args)
    if not args.serial:
        parser.error("--serial is required unless --self-check is used")
    return capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
