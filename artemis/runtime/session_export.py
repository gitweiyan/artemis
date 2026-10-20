"""Rebuild a reviewable evidence package for a finished *or killed* run.

A run that is interrupted (a harness timeout, a Ctrl+C, a laptop sleeping) never reaches
the finalize step, so its trace archive directory is empty and the session row still says
``running``. The DataEngine database, however, already holds everything the run did: every
step with its before/after screenshots, the action spans, and the LLM call ledger.

This module turns that into the same artifacts a normal run produces —
``steps.json``, the images, and a human-readable ``evidence.md`` timeline — so "was it
traceable?" does not depend on the process surviving to the end.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from artemis.runtime.trace_export import manifest_rows

__all__ = ["SessionExport", "export_session_from_db", "resolve_session"]

_SESSION_COLUMNS = "session_id, initial_goal, start_time, end_time, status"


@dataclass
class SessionExport:
    """What the export wrote, and what the database said about the run."""

    session_id: str
    directory: Path
    status: str
    steps: int = 0
    images: int = 0
    actions: int = 0
    incidents: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"session {self.session_id} ({self.status}) -> {self.directory}",
            f"steps={self.steps} actions={self.actions} incidents={self.incidents}"
            f" images={self.images}",
        ]
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)


def resolve_session(db: sqlite3.Connection, selector: str) -> str:
    """``latest`` (newest session), a full id, or an unambiguous id prefix."""
    if selector in ("latest", "last"):
        row = db.execute(
            "SELECT session_id FROM sessions ORDER BY start_time DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("the trace database has no sessions")
        return str(row[0])
    if db.execute("SELECT 1 FROM sessions WHERE session_id=?", (selector,)).fetchone():
        return selector
    rows = db.execute(
        "SELECT session_id FROM sessions WHERE session_id LIKE ?", (f"{selector}%",)
    ).fetchall()
    if not rows:
        raise ValueError(f"no session matches {selector!r}")
    if len(rows) > 1:
        raise ValueError(f"{selector!r} matches {len(rows)} sessions; use more characters")
    return str(rows[0][0])


def _copy_images(db: sqlite3.Connection, image_dir: Path, names: set[str], target: Path) -> int:
    copied = 0
    target.mkdir(parents=True, exist_ok=True)
    for name in sorted(names):
        if not name:
            continue
        for suffix in (".jpg", ".jpeg", ".png"):
            source = image_dir / f"{name}{suffix}"
            if source.is_file():
                shutil.copy2(source, target / source.name)
                copied += 1
                break
    return copied


def _evidence_markdown(session: tuple, steps: list[sqlite3.Row], actions: list[sqlite3.Row]) -> str:
    session_id, goal, start_time, end_time, status = session
    started = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
    ended = datetime.fromtimestamp(end_time).strftime("%H:%M:%S") if end_time else "—（未正常结束）"
    lines = [
        f"# Run evidence — {session_id}",
        "",
        f"- 状态：`{status}`（导出时数据库中记录的状态）",
        f"- 开始：{started}　结束：{ended}",
        f"- 目标：{goal.splitlines()[0][:160]}",
        f"- 步数：{len(steps)}　动作数：{len(actions)}",
        "",
        "## 时间线",
        "",
        "| # | 相对时间 | 动作 | 结果 | incident | 截图 |",
        "|---|---|---|---|---|---|",
    ]
    base = start_time
    for step in steps:
        number, timestamp, pre, post, summary, taken, result = (
            step["step_number"],
            step["timestamp"],
            step["pre_image_name"],
            step["post_image_name"],
            step["summary"],
            step["action_taken"],
            step["last_execution_result"],
        )
        action = _short_action(taken)
        outcome = _short_result(result)
        incident = _incident_of(taken) or _incident_of(result) or ""
        relative = f"+{int(timestamp - base)}s" if timestamp else ""
        images = " / ".join(part for part in (pre or "", post or "") if part)
        lines.append(
            f"| {number} | {relative} | {action} | {outcome} | {incident} | {images[:60]} |"
        )
    if not steps:
        lines.append("| – | – | 数据库中没有步骤记录 | – | – | – |")
    return "\n".join(lines) + "\n"


def _short_action(taken) -> str:
    try:
        actions = json.loads(taken) if isinstance(taken, str) else (taken or [])
    except ValueError:
        return str(taken)[:60]
    if not actions:
        return "—"
    first = actions[0] if isinstance(actions, list) else actions
    if not isinstance(first, dict):
        return str(first)[:60]
    verb = first.get("action") or "?"
    where = first.get("normalized_coordinates") or first.get("coordinates") or first.get("keycode")
    return f"{verb} {where}".strip()[:60]


def _short_result(result) -> str:
    try:
        payload = json.loads(result) if isinstance(result, str) else (result or {})
    except ValueError:
        return str(result)[:60]
    if not isinstance(payload, dict):
        return str(payload)[:60]
    text = str(payload.get("error") or payload.get("result") or payload.get("status") or "")
    return " ".join(text.split())[:70]


def _incident_of(payload) -> str | None:
    try:
        data = json.loads(payload) if isinstance(payload, str) else (payload or {})
    except ValueError:
        return None
    if isinstance(data, dict):
        return data.get("incident")
    return None


def export_session_from_db(
    db_path: Path, selector: str, out_dir: Path, *, images_dir: Path | None = None
) -> SessionExport:
    """Write ``steps.json``, the images and ``evidence.md`` for one session."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise ValueError(f"no trace database at {db_path}")
    out_dir = Path(out_dir)
    image_dir = Path(images_dir) if images_dir else db_path.parent / "images"
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        session_id = resolve_session(db, selector)
        session = db.execute(
            f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        steps = db.execute(
            "SELECT step_number, timestamp, pre_image_name, post_image_name, summary,"
            " action_taken, last_execution_result FROM steps"
            " WHERE session_id=? ORDER BY step_number",
            (session_id,),
        ).fetchall()
        actions = db.execute(
            "SELECT name, status, payload, timestamp FROM traces"
            " WHERE session_id=? AND type='action' ORDER BY timestamp",
            (session_id,),
        ).fetchall()
    finally:
        db.close()

    directory = out_dir / session_id
    directory.mkdir(parents=True, exist_ok=True)
    rows = manifest_rows(
        [
            {
                "step_number": step["step_number"],
                "timestamp": step["timestamp"],
                "action_taken": json.loads(step["action_taken"])
                if isinstance(step["action_taken"], str) and step["action_taken"].strip()
                else step["action_taken"],
                "summary": step["summary"],
                "pre_image_name": step["pre_image_name"],
                "post_image_name": step["post_image_name"],
                "last_execution_result": step["last_execution_result"],
            }
            for step in steps
        ]
    )
    (directory / "steps.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    names = {
        name for step in steps for name in (step["pre_image_name"], step["post_image_name"]) if name
    }
    copied = _copy_images(sqlite3.Connection(":memory:"), image_dir, names, directory / "images")

    incidents = sum(1 for action in actions if _incident_of(action["payload"]))
    (directory / "evidence.md").write_text(
        _evidence_markdown(
            (
                session["session_id"],
                session["initial_goal"],
                session["start_time"],
                session["end_time"],
                session["status"],
            ),
            steps,
            actions,
        ),
        encoding="utf-8",
    )

    notes = []
    if session["status"] == "running" and session["end_time"] is None:
        notes.append(
            "the session row is still 'running': the process was interrupted before finalize,"
            " so this export is the authoritative record"
        )
    return SessionExport(
        session_id=session_id,
        directory=directory,
        status=str(session["status"]),
        steps=len(rows),
        images=copied,
        actions=len(actions),
        incidents=incidents,
        notes=notes,
    )
