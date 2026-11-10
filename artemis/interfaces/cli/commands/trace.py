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

"""Trace inspection and replay commands (artemis trace)."""

import datetime
import json
from pathlib import Path
from typing import Annotated

from artemis.config import settings
from third_party.mobile_use.utils.logger import get_logger
from rich.console import Console
from rich.table import Table
import typer

logger = get_logger(__name__)
trace_app = typer.Typer(help="Inspect, list, and query task execution traces.")


@trace_app.command("list")
def list_traces(
    traces_path: Annotated[
        Path | None,
        typer.Option("--path", "-p", help="Custom traces directory path."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max sessions to list.")] = 20,
) -> None:
    """List recorded execution trace sessions."""
    base_dir = traces_path or settings.TRACES_PATH
    if not base_dir.exists():
        typer.secho(f"No traces found at: {base_dir}", fg=typer.colors.YELLOW)
        return

    sessions = [d for d in base_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    sessions.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    console = Console()
    table = Table(title=f"Recorded Traces ({len(sessions)} total)")
    table.add_column("Session Name", style="cyan")
    table.add_column("Last Modified", style="green")
    table.add_column("Artifacts", style="white")

    for s in sessions[:limit]:
        artifacts = []
        if (s / "recording.mp4").exists() or (s / "recording.mkv").exists():
            artifacts.append("🎬 Video")
        if manifest_has_steps(s):
            artifacts.append("📋 Steps")
        if (s / "notes").exists():
            artifacts.append("📝 Notes")
        mtime = time_str = time_to_str(s.stat().st_mtime)
        table.add_row(s.name, mtime, ", ".join(artifacts) or "Screenshots")

    console.print(table)


def manifest_has_steps(session_dir: Path) -> bool:
    """Whether the session's ``steps.json`` actually lists steps.

    The file exists in every modern archive and is empty in most of them, so
    "exists" told a reader the opposite of the truth (I7).
    """
    manifest = session_dir / "steps.json"
    if not manifest.is_file():
        return False
    try:
        steps = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(steps, list) and bool(steps)


def time_to_str(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


@trace_app.command("export")
def export_trace(
    session: Annotated[
        str, typer.Argument(help="Session id (or an unambiguous prefix), or 'latest'.")
    ] = "latest",
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Directory for the export.")
    ] = None,
) -> None:
    """Rebuild a reviewable evidence package from the trace database.

    Works for runs that were interrupted before the normal archive was written (a killed
    process, a harness timeout): the database already holds the steps, screenshots and
    action spans.
    """
    from artemis.runtime.session_export import export_session_from_db

    traces_dir = Path(settings.TRACES_PATH)
    db_path = traces_dir / "data_engine.db"
    try:
        export = export_session_from_db(db_path, session, out or (traces_dir / "exports"))
    except ValueError as exc:
        typer.secho(f"Export failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)
    console = Console()
    console.print(export.summary())
    console.print(f"timeline: {export.directory / 'evidence.md'}")


@trace_app.command("view")
def view_trace(
    session_name: Annotated[str, typer.Argument(help="Name of the trace session directory.")],
    traces_path: Annotated[Path | None, typer.Option("--path", "-p")] = None,
) -> None:
    """View step-by-step summary of a recorded trace session."""
    base_dir = traces_path or settings.TRACES_PATH
    session_dir = base_dir / session_name

    if not session_dir.exists():
        typer.secho(f"Session '{session_name}' not found in {base_dir}", fg=typer.colors.RED)
        raise typer.Exit(1)

    steps_file = session_dir / "steps.json"
    console = Console()

    if manifest_has_steps(session_dir):
        try:
            steps_data = json.loads(steps_file.read_text(encoding="utf-8"))
            table = Table(title=f"Trace Steps: {session_name}")
            table.add_column("Step", justify="right", style="cyan")
            table.add_column("Action", style="green")
            table.add_column("Reasoning / Motivation", style="white")

            for step in steps_data:
                table.add_row(
                    str(step.get("step", "-")),
                    step.get("action", "-"),
                    step.get("motivation", step.get("thought", "-")),
                )
            console.print(table)
            return
        except Exception as e:
            logger.warning(f"Could not parse steps.json: {e}")

    # If steps.json not present, list files
    if steps_file.exists():
        typer.secho(
            f"{session_name}: steps.json lists no steps — the archive holds video only.",
            fg=typer.colors.YELLOW,
        )
    typer.secho(f"Session files in {session_dir}:", fg=typer.colors.CYAN)
    for f in session_dir.iterdir():
        typer.echo(f"  - {f.name}")


@trace_app.command("actions")
def query_actions(
    session: Annotated[
        str, typer.Option("--session", "-s", help="Session id/prefix or latest.")
    ] = "latest",
    action_id: Annotated[
        str | None, typer.Option("--action-id", help="Filter by action id.")
    ] = None,
    device_id: Annotated[str | None, typer.Option("--device", help="Filter by device id.")] = None,
    artifact_id: Annotated[
        str | None, typer.Option("--artifact", help="Filter by artifact hash.")
    ] = None,
    traces_path: Annotated[Path | None, typer.Option("--path", "-p")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l")] = 50,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show correlated action evidence (what was observed, dispatched and verified)."""

    import sqlite3

    from artemis.data_engine.replay import query_action_evidence
    from artemis.data_engine.storage import StorageManager

    base_dir = Path(traces_path) if traces_path else Path(settings.TRACES_PATH)
    db_path = base_dir / "data_engine.db"
    if not db_path.is_file():
        typer.secho(f"No trace database at {db_path}", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    if session == "latest":
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                row = conn.execute(
                    "SELECT session_id FROM sessions ORDER BY start_time DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error as exc:
            typer.secho(f"Could not read sessions: {exc}", fg=typer.colors.RED)
            raise typer.Exit(1)
        if row is None:
            typer.secho("No sessions in the trace database.", fg=typer.colors.YELLOW)
            raise typer.Exit(1)
        resolved = str(row[0])
    else:
        resolved = session

    storage = StorageManager(db_path, base_dir, read_only=True)
    traces = storage.get_traces_for_session(
        resolved, trace_type="action", limit=max(limit * 10, 100)
    )
    evidence = query_action_evidence(
        traces,
        action_id=action_id,
        session_id=resolved,
        device_id=device_id,
        artifact_id=artifact_id,
        limit=limit,
    )
    if as_json:
        typer.echo(json.dumps(evidence, ensure_ascii=False, indent=2))
        return
    if not evidence:
        typer.secho(f"No action evidence for session {resolved}.", fg=typer.colors.YELLOW)
        return
    table = Table(title=f"Action evidence · {resolved} · {len(evidence)} action(s)")
    table.add_column("Action", style="cyan")
    table.add_column("Device")
    table.add_column("Adapter")
    table.add_column("ACK")
    table.add_column("Incident")
    table.add_column("Post verification", overflow="fold")
    for item in evidence:
        ids = item.get("ids") or {}
        post = item.get("post_verification") or {}
        table.add_row(
            str(ids.get("action_id") or "-"),
            str(ids.get("device_id") or "-"),
            str(item.get("adapter_id") or "-"),
            str(item.get("device_ack") or "-"),
            str(item.get("incident") or "none"),
            str(post.get("incident") or post.get("allowed", "n/a")),
        )
    Console().print(table)
