# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Reflex skills from the command line (``artemis reflex``).

Two jobs: show which skills exist and why one does not load (``skills``), and run
one on a device to get real numbers (``probe``). The second is the answer to "can
this device drive a 5–10 Hz loop at all?" — before any game logic is blamed.
"""

from __future__ import annotations

import json
from typing import Annotated

from artemis.reflex import registry
from artemis.reflex.capture import default_capture
from artemis.reflex.control import AdbInputControl, HelperControl
from artemis.reflex.runtime import DEFAULT_FPS_CAP, ReflexRuntime
from rich.console import Console
from rich.table import Table
import typer

reflex_app = typer.Typer(help="Inspect and run reflex skills (fast, model-free inner loops).")


@reflex_app.command("skills")
def list_skills(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show each plan hint.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List the reflex skills on this machine and whether each one loads."""
    root = registry.skills_dir()
    found = registry.available_skills()
    if as_json:
        typer.echo(
            json.dumps({"skills_dir": str(root), "skills": found}, ensure_ascii=False, indent=2)
        )
        return
    if not found:
        typer.secho(
            f"No reflex skills under {root}"
            " (add a directory with a skill.json, or point ARTEMIS_SKILLS_DIR at yours).",
            fg=typer.colors.YELLOW,
        )
        return

    table = Table(title=f"Reflex skills · {root}")
    table.add_column("Skill", style="cyan")
    table.add_column("Version")
    table.add_column("Calibrated")
    table.add_column("Hint / error", overflow="fold")
    for name, entry in sorted(found.items()):
        if not entry.get("ok"):
            table.add_row(name, "-", "-", f"[red]BROKEN[/red] {entry.get('error')}")
            continue
        detail = entry.get("plan_hint") if verbose else ""
        table.add_row(
            name,
            str(entry.get("version") or "-"),
            "yes" if entry.get("calibrated") else "no",
            str(detail or ""),
        )
    Console().print(table)


@reflex_app.command("probe")
def probe(
    skill: Annotated[str, typer.Option("--skill", "-s", help="Skill to run.")] = "noop",
    serial: Annotated[str | None, typer.Option("--serial", help="Device serial.")] = None,
    seconds: Annotated[float, typer.Option("--seconds", help="Wall-clock budget.")] = 10.0,
    fps_cap: Annotated[float, typer.Option("--fps-cap", help="Loop rate cap.")] = DEFAULT_FPS_CAP,
    capture: Annotated[str, typer.Option("--capture", help="'helper' or 'screencap'.")] = "helper",
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Run a reflex skill on the device and report frames, latency and stop reason."""
    target = serial or _resolve_serial()
    if not target:
        typer.secho("No device found (connect one, or pass --serial).", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    try:
        handle = registry.load_skill(skill)
    except registry.SkillError as exc:
        typer.secho(f"Skill not loadable: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    frame_source = default_capture(target, prefer=capture)
    actuator = HelperControl(target) if capture == "helper" else AdbInputControl(target)
    runtime = ReflexRuntime(capture=frame_source, control=actuator, fps_cap=fps_cap)
    typer.echo(f"Running reflex skill '{skill}' on {target} for {seconds:g}s ({capture} capture)…")
    result = runtime.run(handle, seconds=seconds)

    if as_json:
        typer.echo(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        raise typer.Exit(code=0 if result.stop_reason.value != "skill_error" else 1)

    table = Table(title=f"Reflex probe · {skill} · {target}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value")
    for label, value in (
        ("Stop reason", result.stop_reason.value),
        ("Seconds", f"{result.seconds:.2f}"),
        ("Frames", str(result.frames)),
        ("Frames/s", f"{result.fps:.2f}"),
        ("Actions", str(result.actions)),
        ("Capture p50 / p95", f"{result.capture_ms_p50:.1f} ms / {result.capture_ms_p95:.1f} ms"),
        ("Action p50 / p95", f"{result.action_ms_p50:.1f} ms / {result.action_ms_p95:.1f} ms"),
        ("Blind frames", str(result.blind_frames)),
        ("Control failures", str(result.control_failures)),
    ):
        table.add_row(label, value)
    if result.counters:
        table.add_row("Counters", ", ".join(f"{k}={v}" for k, v in result.counters.items()))
    if result.skill_error:
        table.add_row("Skill error", result.skill_error)
    Console().print(table)
    if result.stop_reason.value == "lost":
        typer.secho(
            "The loop could not read the screen at all — check the device/capture path.",
            fg=typer.colors.RED,
        )


@reflex_app.command("calibrate")
def calibrate(
    skill: Annotated[str, typer.Option("--skill", "-s", help="Skill to calibrate.")] = "tetris",
    serial: Annotated[str | None, typer.Option("--serial", help="Device serial.")] = None,
    frames: Annotated[int, typer.Option("--frames", help="Frames to capture.")] = 3,
    gap: Annotated[float, typer.Option("--gap", help="Seconds between frames.")] = 1.0,
    capture: Annotated[str, typer.Option("--capture", help="'helper' or 'screencap'.")] = "helper",
    launch: Annotated[str | None, typer.Option("--launch", help="Package to launch first.")] = None,
    keep: Annotated[bool, typer.Option("--keep/--no-keep", help="Save sample frames.")] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Capture real frames and print what perception sees (grid, palette, board).

    A vision skill is only as good as its calibration, and calibration needs eyes on
    the screen. The board is rendered as text on purpose: that is how a wrong palette
    or a shifted grid becomes visible without opening the image.
    """
    import sys

    target = serial or _resolve_serial()
    if not target:
        typer.secho("No device found (connect one, or pass --serial).", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    try:
        handle = registry.load_skill(skill)
    except registry.SkillError as exc:
        typer.secho(f"Skill not loadable: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    if not callable(handle.arena) and handle.calibration.get("_no_arena"):
        raise typer.Exit(code=1)

    package = launch or (handle.calibration.get("package") or "")
    if package:
        import subprocess

        subprocess.run(
            [
                "adb",
                "-s",
                target,
                "shell",
                "monkey",
                "-p",
                package,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        typer.echo(f"Launched {package}; waiting 5 s for it to settle…")
        import time

        time.sleep(5.0)

    module = sys.modules.get(f"artemis_skills_{skill}_calibrate")
    if module is None:
        import importlib

        try:
            module = importlib.import_module(f"artemis_skills_{skill}.calibrate")
        except ImportError:
            try:
                module = importlib.import_module(f"artemis_skills_{skill}.calibrate")
            except ImportError as exc:
                typer.secho(f"Skill '{skill}' ships no calibrate.py: {exc}", fg=typer.colors.RED)
                raise typer.Exit(code=1) from exc

    source = default_capture(target, prefer=capture)
    try:
        images = module.settle_and_capture(source, frames=frames, gap_s=gap)
    finally:
        source.close()

    samples = handle.directory / module.SAMPLES_DIR
    reports = []
    for index, image in enumerate(images):
        if keep:
            path = module.save_frame(image, directory=samples, tag=f"calib-{index + 1}")
            typer.echo(f"sample saved: {path}")
        report = module.analyse(image, grid=None)
        report["sample"] = f"calib-{index + 1}"
        reports.append(report)

    if as_json:
        typer.echo(module.json_report(reports if len(reports) > 1 else reports[0]))
        return
    for report in reports:
        typer.echo("")
        typer.echo(module.render_report(report))
    typer.echo("")
    typer.echo("calibration proposal (paste into calibration.json, then correct by hand):")
    typer.echo(json.dumps(module.proposal(reports[-1]), ensure_ascii=False, indent=2))


@reflex_app.command("arena")
def arena(
    skill: Annotated[
        str, typer.Option("--skill", "-s", help="Skill whose arena to run.")
    ] = "tetris",
    games: Annotated[int, typer.Option("--games", help="Seeded games per policy.")] = 20,
    seed: Annotated[
        int, typer.Option("--seed", help="First seed (games use seed, seed+1, …).")
    ] = 1,
    max_pieces: Annotated[int, typer.Option("--max-pieces", help="Cap pieces per game.")] = 500,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Run a skill's offline arena: no device, no model, reproducible comparisons.

    This is how a policy change (or a model-backed policy) is judged on numbers
    before it is allowed near a real game.
    """
    try:
        handle = registry.load_skill(skill)
    except registry.SkillError as exc:
        typer.secho(f"Skill not loadable: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    if not callable(handle.arena):
        typer.secho(
            f"Skill '{skill}' ships no arena (add entry.arena to its skill.json).",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    report = handle.arena(games=games, seed=seed, max_pieces=max_pieces)
    if as_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return

    table = Table(title=f"Reflex arena · {skill} · {report.get('games_per_policy')} games/policy")
    table.add_column("Policy", style="cyan")
    table.add_column("Mean lines")
    table.add_column("Median")
    table.add_column("Min–max")
    table.add_column("Topped out")
    table.add_column("Decision ms")
    for name, entry in (report.get("policies") or {}).items():
        table.add_row(
            str(name),
            f"{entry['mean_lines']}",
            f"{entry['median_lines']}",
            f"{entry['min_lines']}–{entry['max_lines']}",
            f"{entry['topped_out']}/{entry['games']}",
            f"{entry['mean_decision_ms']}",
        )
    Console().print(table)


def _resolve_serial() -> str | None:
    """The first attached device, or None."""
    import subprocess

    try:
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


__all__ = ["reflex_app"]
