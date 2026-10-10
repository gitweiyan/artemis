"""I7 · the archive must carry the run's steps, not only its video.

The 2026-10-09 archives (both retrospectives) shipped `steps.json = []` and no
images, because the compiler reads the folder scrcpy writes to while the steps
live in ``data_engine.db``, the screenshots in the content-addressed
``traces/images/`` store and the notes in the session directory. Silent, too: the
only trace of it was an INFO line, and ``run_outcome`` said nothing.

Two call sites, two moments (the legacy compiler rewrites ``steps.json`` from the
folder, so the real manifest has to be written *after* it runs):

* :func:`export_step_media` — before the compile: one ``<step>.jpeg`` per step
  (what the GIF builder scans) plus a copy of ``notes/``;
* :func:`write_step_manifest` — after the compile/rename: the real manifest, in a
  schema both the CLI viewer (``step`` / ``action`` / ``motivation``) and the
  legacy reader (``timestamp``) can read.
"""

from __future__ import annotations

import importlib
import json
import os
from types import SimpleNamespace

import pytest

from third_party.mobile_use.utils.media import (
    create_steps_json_from_trace_folder,
    create_gif_from_trace_folder,
)

SESSION_ID = "11111111-2222-3333-4444-555555555555"


def _module():
    """The exporter, failing cleanly while it does not exist yet."""
    try:
        module = importlib.import_module("artemis.runtime.trace_export")
    except ImportError as exc:
        raise AssertionError(f"the trace exporter is missing: {exc}") from exc
    return module


class FakeEngine:
    """The narrow slice of DataEngine the exporter uses.

    ``base_dir`` is faithful to the real property (``engine.py``): it is *already*
    the session directory — ``global_base_dir / session_id`` — which is exactly
    where ``artemis/utils/notes.py`` and the diagnoser keep ``notes/``.
    """

    def __init__(self, tmp_path, *, steps: int = 3, images: bool = True, notes: bool = True):
        self.current_session_id = SESSION_ID
        self.global_base_dir = tmp_path / "traces"
        self.base_dir = self.global_base_dir / SESSION_ID
        session_dir = self.base_dir
        (session_dir / "notes").mkdir(parents=True, exist_ok=True)
        if notes:
            (session_dir / "notes" / "task_plan.md").write_text("- [x] done", encoding="utf-8")
        self.images_dir = self.global_base_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._steps: list[dict] = []
        for number in range(1, steps + 1):
            name = None
            if images:
                name = f"hash{number}"
                (self.images_dir / f"{name}.jpg").write_bytes(b"jpeg-bytes")
            self._steps.append(
                {
                    "step_number": number,
                    "timestamp": 1000.0 + number,
                    "relative_time": f"T+00:0{number}",
                    "action_taken": [{"action": "tap", "coordinates": [10 * number, 20 * number]}],
                    "operator_raw_thinking": f"thinking about step {number}",
                    "summary": None,
                    "pre_image_name": None,
                    "post_image_name": name,
                    "last_execution_result": {"status": "success"},
                    "extra_metadata": {},
                }
            )

    def get_agent_friendly_steps(self) -> list[dict]:
        return list(self._steps)

    def get_image_path(self, image_name: str):
        """``DataEngine.get_image_path``: the content-addressed store path."""
        return self.images_dir / f"{image_name}.jpg"

    def get_step_image_path(self, step_number: int, which: str = "pre"):
        row = next((s for s in self._steps if s["step_number"] == step_number), None)
        if row is None:
            return None
        name = row["post_image_name" if which == "post" else "pre_image_name"]
        return (self.images_dir / f"{name}.jpg") if name else None


def _ctx(tmp_path, **kwargs) -> SimpleNamespace:
    return SimpleNamespace(data_engine=FakeEngine(tmp_path, **kwargs))


def test_the_manifest_carries_what_both_consumers_read(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    ctx = _ctx(tmp_path)

    _module().write_step_manifest(ctx, archive)

    steps = json.loads((archive / "steps.json").read_text(encoding="utf-8"))
    assert len(steps) == 3
    first = steps[0]
    # CLI viewer reads these three …
    assert first["step"] == 1
    assert first["action"]
    assert first["motivation"] == "thinking about step 1"
    # … and the legacy reader reads this one.
    assert first["timestamp"] == 1001.0
    assert first["post_image"] == "images/hash1.jpg"


def test_media_places_one_frame_per_step_for_the_gif_builder(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    ctx = _ctx(tmp_path)

    report = _module().export_step_media(ctx, archive)

    frames = sorted(p.name for p in archive.glob("*.jpeg"))
    assert frames == ["1.jpeg", "2.jpeg", "3.jpeg"]
    assert report.images == 3 and report.steps == 3
    # The legacy scanner requires a numeric stem and a .jpeg suffix.
    assert all(int(p.stem) for p in archive.glob("*.jpeg"))
    # Hardlink when the filesystem allows it, copy otherwise — never a symlink
    # to a store that a moved archive would lose.
    assert not (archive / "1.jpeg").is_symlink()
    assert os.path.samefile(archive / "1.jpeg", tmp_path / "traces/images/hash1.jpg")


def test_the_manifest_image_references_resolve_inside_the_archive(tmp_path):
    """``images/<sha256>.jpg`` has to be a path a reader can open.

    The first hardware run of this exporter referenced the content-addressed
    store by name and placed nothing: ``pre_image`` pointed at an ``images/``
    directory that did not exist. The GIF is the moving picture, the store is
    shared and prunable, so each referenced still travels with the archive (as
    hardlinks — the frames are already on disk).
    """
    archive = tmp_path / "archive"
    archive.mkdir()
    ctx = _ctx(tmp_path)
    _module().export_step_media(ctx, archive)
    _module().write_step_manifest(ctx, archive)

    steps = json.loads((archive / "steps.json").read_text(encoding="utf-8"))
    referenced = [ref for step in steps for ref in (step["pre_image"], step["post_image"]) if ref]
    assert referenced, "the manifest named no image at all"
    missing = [ref for ref in referenced if not (archive / ref).is_file()]
    assert not missing, f"the manifest points at files the archive does not hold: {missing}"
    # Still the content-addressed store: a hardlink, not a copy or a symlink.
    assert os.path.samefile(archive / referenced[0], tmp_path / "traces/images/hash1.jpg")


def test_the_archive_keeps_no_frame_the_compiler_deletes(tmp_path):
    """Frames placed for the GIF builder are consumed; the stills are not.

    ``remove_images_from_trace_folder`` unlinks top-level ``*.jpeg`` only, so the
    per-step stills have to live somewhere it does not touch.
    """
    from third_party.mobile_use.utils.media import remove_images_from_trace_folder

    archive = tmp_path / "archive"
    archive.mkdir()
    _module().export_step_media(_ctx(tmp_path), archive)
    remove_images_from_trace_folder(archive)

    assert not list(archive.glob("*.jpeg"))
    assert (archive / "images" / "hash1.jpg").is_file()


def test_the_gif_builder_scans_the_frames_we_place(tmp_path, caplog, monkeypatch):
    """The point of the <step>.jpeg contract: the existing scanner finds them.

    Only the encode step is stubbed — the frames here are stand-ins, not JPEGs —
    while the scan under test stays the real one.
    """
    import logging

    from third_party.mobile_use.utils import media

    monkeypatch.setattr(media, "quantize_and_save_gif_from_paths", lambda *_a, **_k: None)
    archive = tmp_path / "archive"
    archive.mkdir()
    ctx = _ctx(tmp_path)
    _module().export_step_media(ctx, archive)

    with caplog.at_level(logging.INFO):
        create_gif_from_trace_folder(archive)

    assert "Found 3 images to compile" in caplog.text


def test_notes_travel_with_the_archive(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()

    report = _module().export_step_media(_ctx(tmp_path), archive)

    assert (archive / "notes" / "task_plan.md").read_text(encoding="utf-8") == "- [x] done"
    assert report.notes == 1


def test_the_manifest_is_written_after_the_legacy_compiler_so_it_survives(tmp_path):
    """The legacy compiler rewrites steps.json from the folder — empty here."""
    archive = tmp_path / "archive"
    archive.mkdir()
    ctx = _ctx(tmp_path)
    _module().export_step_media(ctx, archive)

    create_steps_json_from_trace_folder(archive)
    assert json.loads((archive / "steps.json").read_text(encoding="utf-8")) == []

    _module().write_step_manifest(ctx, archive)

    steps = json.loads((archive / "steps.json").read_text(encoding="utf-8"))
    assert len(steps) == 3, "the real manifest must win over the legacy empty write"


def test_a_session_with_steps_but_no_media_is_reported_as_a_degradation(tmp_path, caplog):
    archive = tmp_path / "archive"
    archive.mkdir()
    ctx = _ctx(tmp_path, images=False)

    import logging

    from artemis.runtime import degradations

    with caplog.at_level(logging.WARNING):
        _module().export_step_media(ctx, archive)

    assert "archive" in caplog.text.lower()
    summary = degradations.log_for(ctx).summary()
    assert summary["counts"].get(degradations.ARCHIVE_INCOMPLETE) == 1
    assert summary["capability_degraded"] is True


def test_a_session_without_steps_exports_nothing_and_stays_quiet(tmp_path, caplog):
    archive = tmp_path / "archive"
    archive.mkdir()
    ctx = _ctx(tmp_path, steps=0, images=False, notes=False)

    import logging

    from artemis.runtime import degradations

    with caplog.at_level(logging.WARNING):
        report = _module().export_step_media(ctx, archive)
        _module().write_step_manifest(ctx, archive)

    assert report.steps == 0
    assert not (archive / "steps.json").exists()
    assert degradations.log_for(ctx).summary()["total"] == 0
    assert not caplog.records


def _settled_outcome(tmp_path) -> dict:
    """A run that already wrote its verdict — the graph settles before the archive."""
    session = tmp_path / "traces" / SESSION_ID
    session.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_status": "completed",
        "tests": {"passed": 2, "failed": 0},
        "environment": {
            "backend": "uiautomator",
            "degradations": {
                "counts": {},
                "total": 0,
                "examples": {},
                "capability_degraded": False,
            },
        },
    }
    (session / "run_outcome.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_an_incomplete_archive_reaches_the_verdict_written_before_it(tmp_path):
    """``run_outcome`` is settled by the graph; the archive is compiled after it.

    Without amending, the export's one failure mode is recorded on a context
    nobody reads again — the silent archive of 2026-10-09 all over again.
    """
    archive = tmp_path / "archive"
    archive.mkdir()
    before = _settled_outcome(tmp_path)
    ctx = _ctx(tmp_path, images=False)

    _module().export_step_media(ctx, archive)

    after = json.loads(
        (tmp_path / "traces" / SESSION_ID / "run_outcome.json").read_text(encoding="utf-8")
    )
    degradations_block = after["environment"]["degradations"]
    assert degradations_block["counts"]["archive_incomplete"] == 1
    assert degradations_block["capability_degraded"] is True
    # Amending adds the finding; it does not rewrite the verdict.
    assert after["task_status"] == before["task_status"]
    assert after["tests"] == before["tests"]
    assert after["environment"]["backend"] == "uiautomator"


def test_a_complete_archive_leaves_the_verdict_untouched(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    _settled_outcome(tmp_path)
    path = tmp_path / "traces" / SESSION_ID / "run_outcome.json"
    original = path.read_text(encoding="utf-8")

    _module().export_step_media(_ctx(tmp_path), archive)
    _module().write_step_manifest(_ctx(tmp_path), archive)

    assert path.read_text(encoding="utf-8") == original


def test_an_incomplete_archive_without_a_verdict_is_harmless(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    ctx = _ctx(tmp_path, images=False)

    report = _module().export_step_media(ctx, archive)

    assert report.steps == 3
    assert not (tmp_path / "traces" / SESSION_ID / "run_outcome.json").exists()
