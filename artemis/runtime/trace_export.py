"""I7 · materialise a run's steps, frames and notes into its archive.

What went wrong: the archive compiler reads the folder scrcpy writes to
(``traces/web_<epoch>_<sid>/``) while the run keeps its steps in
``data_engine.db``, its screenshots in the content-addressed ``traces/images/``
store and its notes in the session directory. Every archive therefore shipped
``steps.json = []`` and no frames — silently, at INFO level, with nothing in
``run_outcome``. Both 2026-10-09 retrospectives had to read the database because
their own archives could not answer anything.

The legacy scanners cannot be pointed at the store either: the GIF builder wants
``*.jpeg`` files with numeric stems in the archive folder, and it *rewrites*
``steps.json`` from whatever it finds there. So this module supplies both, in the
two moments the compile flow allows:

* :func:`export_step_media` before the compile — ``<step>.jpeg`` frames (hardlinked
  from the store, copied when the filesystem refuses) plus a ``notes/`` copy;
* :func:`write_step_manifest` after the compile — the real manifest, written last
  so the legacy empty write cannot win.

The manifest schema serves both readers in the tree: the CLI viewer's
``step`` / ``action`` / ``motivation`` and the legacy reader's ``timestamp``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any

from artemis.runtime import degradations
from artemis.utils.task_tree import format_action_clean
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: The legacy GIF builder scans the archive top level for ``*.jpeg`` and sorts by
#: ``int(stem)``; the frames are removed again once the GIF exists.
FRAME_SUFFIX = ".jpeg"
#: Store references live in a subdirectory: the legacy scanner never descends.
IMAGES_SUBDIR = "images"
MANIFEST_NAME = "steps.json"
MOTIVATION_CHARS = 600
#: What reading the run's own store may raise — an absent file, a malformed row, a
#: column this version does not know. Deliberately narrow: an archive is written
#: after the run and must never break it (the caller wraps finalization too), but
#: swallowing an unexpected defect would recreate the silent archive of I7.
_STORE_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError)


@dataclass(frozen=True)
class MediaReport:
    """What :func:`export_step_media` managed to put in the archive."""

    steps: int
    #: Frames placed for the GIF builder (``<step>.jpeg``), consumed by the compile.
    images: int
    #: Per-step stills kept under ``images/``: what the manifest references.
    stills: int
    notes: int

    def as_dict(self) -> dict[str, int]:
        return {
            "steps": self.steps,
            "images": self.images,
            "stills": self.stills,
            "notes": self.notes,
        }


def _engine(ctx: Any) -> Any:
    return getattr(ctx, "data_engine", None)


def _steps(engine: Any) -> list[dict]:
    getter = getattr(engine, "get_agent_friendly_steps", None)
    if not callable(getter):
        return []
    try:
        return list(getter() or [])
    except _STORE_ERRORS as exc:
        logger.warning(f"Trace archive: could not read the session's steps: {exc}")
        return []


def _step_source_image(engine: Any, number: int) -> Path | None:
    """The screenshot to place as this step's frame: after the action, else before."""
    getter = getattr(engine, "get_step_image_path", None)
    if not callable(getter):
        return None
    for which in ("post", "pre"):
        try:
            candidate = getter(number, which)
        except _STORE_ERRORS:
            continue
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def _link_or_copy(source: Path, target: Path) -> bool:
    """Place ``source`` at ``target``: a hardlink when possible, else a copy.

    Never a symlink: an archive that is moved or copied elsewhere must still hold
    its frames.
    """
    try:
        if target.exists():
            target.unlink()
        try:
            os.link(source, target)
            return True
        except OSError:
            shutil.copy2(source, target)
            return True
    except OSError as exc:
        logger.debug(f"Trace archive: could not place {source.name}: {exc}")
        return False


def _copy_notes(engine: Any, archive_dir: Path) -> int:
    """Copy the session's ``notes/`` next to the archive's video.

    ``DataEngine.base_dir`` is already ``traces/<session_id>`` — the same base
    ``artemis/utils/notes.py`` writes under — so appending the session id again
    would silently find nothing.
    """
    base = getattr(engine, "base_dir", None)
    if not base:
        return 0
    source = Path(base) / "notes"
    if not source.is_dir():
        return 0
    target = archive_dir / "notes"
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for note in sorted(source.glob("*")):
        if note.is_file():
            try:
                shutil.copy2(note, target / note.name)
                copied += 1
            except OSError as exc:
                logger.debug(f"Trace archive: could not copy note {note.name}: {exc}")
    return copied


def _place_stills(engine: Any, steps: list[dict], archive_dir: Path) -> int:
    """Hardlink every image the manifest will reference into ``<archive>/images/``.

    A pure name reference would be a dangling path: the store lives at the traces
    root and is shared between sessions (and prunable), while the archive is a
    directory a reader may move. Hardlinks keep the reference resolvable for no
    extra bytes, and ``remove_images_from_trace_folder`` — which unlinks top-level
    ``*.jpeg`` only — never touches them.
    """
    getter = getattr(engine, "get_image_path", None)
    if not callable(getter):
        return 0
    target_dir = archive_dir / IMAGES_SUBDIR
    placed = 0
    for step in steps:
        for key in ("pre_image_name", "post_image_name"):
            name = step.get(key)
            if not name:
                continue
            target = target_dir / f"{name}.jpg"
            if target.exists():
                continue
            try:
                source = Path(getter(name))
            except _STORE_ERRORS as exc:
                logger.debug(f"Trace archive: no store path for image {name}: {exc}")
                continue
            if not source.is_file():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            if _link_or_copy(source, target):
                placed += 1
    return placed


def export_step_media(ctx: Any, archive_dir: Path) -> MediaReport:
    """Put the run's frames and notes into the folder that is about to be archived.

    Call this *before* the tracer compiles the folder: the frames are what its GIF
    builder scans for.
    """
    archive_dir = Path(archive_dir)
    engine = _engine(ctx)
    steps = _steps(engine) if engine is not None else []
    images = 0
    for step in steps:
        number = step.get("step_number")
        if not isinstance(number, int):
            continue
        source = _step_source_image(engine, number)
        if source is not None and _link_or_copy(source, archive_dir / f"{number}{FRAME_SUFFIX}"):
            images += 1
    stills = _place_stills(engine, steps, archive_dir) if engine is not None else 0
    notes = _copy_notes(engine, archive_dir) if engine is not None else 0

    report = MediaReport(steps=len(steps), images=images, stills=stills, notes=notes)
    if report.steps and not report.images:
        logger.warning(
            f"Trace archive: {report.steps} step(s) were recorded but no screenshot could be "
            f"placed in {archive_dir}; the archived trace will carry no frames."
        )
        degradations.record(ctx, degradations.ARCHIVE_INCOMPLETE, f"{report.steps} steps, 0 frames")
        degradations.amend_run_outcome(ctx)
    logger.info(f"Trace archive media: {report.as_dict()}")
    return report


def _image_ref(step: dict, which: str) -> str | None:
    name = step.get("post_image_name" if which == "post" else "pre_image_name")
    return f"{IMAGES_SUBDIR}/{name}.jpg" if name else None


def _status(step: dict) -> str:
    result = step.get("last_execution_result")
    if isinstance(result, dict):
        for key in ("status", "success", "error_msg"):
            value = result.get(key)
            if value not in (None, ""):
                return str(value)
    actions = step.get("action_taken")
    if isinstance(actions, list) and actions and isinstance(actions[0], dict):
        ok = actions[0].get("success")
        if isinstance(ok, bool):
            return "success" if ok else "failed"
    return "unknown"


def _manifest_row(step: dict) -> dict[str, Any]:
    motivation = step.get("operator_raw_thinking") or step.get("operator_native_thinking") or ""
    row: dict[str, Any] = {
        "step": step.get("step_number"),
        "timestamp": step.get("timestamp"),
        "relative_time": step.get("relative_time"),
        "action": format_action_clean(step.get("action_taken"))
        if step.get("action_taken")
        else "No Action",
        "motivation": " ".join(str(motivation).split())[:MOTIVATION_CHARS],
        "status": _status(step),
        "summary": step.get("summary"),
        "pre_image": _image_ref(step, "pre"),
        "post_image": _image_ref(step, "post"),
    }
    metrics = {
        key: value
        for key, value in (step.get("extra_metadata") or {}).items()
        if key in ("observation_skew_s", "foreground_app")
    }
    if metrics:
        row["metrics"] = metrics
    return row


def write_step_manifest(ctx: Any, archive_dir: Path) -> dict[str, Any]:
    """Write the run's real ``steps.json`` into the finished archive.

    Call this *after* the tracer compiled the folder: its legacy scan rewrites
    ``steps.json`` from the folder's digit-named files (an empty list in a modern
    run), so the real manifest has to land last.
    """
    engine = _engine(ctx)
    steps = _steps(engine) if engine is not None else []
    if not steps:
        return {"steps": 0, "written": False}
    rows = [_manifest_row(step) for step in steps]
    path = Path(archive_dir) / MANIFEST_NAME
    try:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Trace archive: could not write {path}: {exc}")
        degradations.record(ctx, degradations.ARCHIVE_INCOMPLETE, f"manifest write failed: {exc}")
        degradations.amend_run_outcome(ctx)
        return {"steps": 0, "written": False}
    logger.info(f"Trace archive: wrote {len(rows)} step(s) to {path}")
    return {"steps": len(rows), "written": True}


__all__ = [
    "FRAME_SUFFIX",
    "MANIFEST_NAME",
    "MediaReport",
    "export_step_media",
    "write_step_manifest",
]
