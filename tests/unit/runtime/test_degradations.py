"""I3 · runtime degradations must reach the run's own conclusion.

Why this exists: the 2026-10-09 18:32 run lost its memory-compression prompt path
9 times and its visual step summaries 8 times, finished ``completed`` with
``passed=2``, and said nothing about it — a reader of ``run_outcome.json`` cannot
tell a full-capability run from a crippled one. The same silence hid the 14:30
run's 7 degradations. This module counts them and folds them, next to the code
identity and the existing backend summary, into ``environment``.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace


def _module():
    """The degradation bookkeeping, failing cleanly until it exists."""
    try:
        module = importlib.import_module("artemis.runtime.degradations")
    except ImportError as exc:
        raise AssertionError(f"degradation bookkeeping is missing: {exc}") from exc
    return module


dg = None


def test_recording_counts_kinds_and_keeps_one_example_each():
    dg = _module()
    log = dg.DegradationLog()

    log.record(dg.TRANSCRIPT_FALLBACK, "ChatGoogleGenerativeAI: API key required")
    log.record(dg.TRANSCRIPT_FALLBACK, "again")
    log.record(dg.VISUAL_SUMMARY_FAILURE, "no lens on this run")

    summary = log.summary()

    assert summary["counts"] == {dg.TRANSCRIPT_FALLBACK: 2, dg.VISUAL_SUMMARY_FAILURE: 1}
    assert summary["total"] == 3
    assert summary["capability_degraded"] is True
    assert "API key required" in summary["examples"][dg.TRANSCRIPT_FALLBACK]


def test_an_untouched_log_reports_no_degradation():
    dg = _module()
    summary = dg.DegradationLog().summary()

    assert summary["counts"] == {}
    assert summary["total"] == 0
    assert summary["capability_degraded"] is False


def test_the_log_is_per_context_not_global():
    dg = _module()
    first, second = SimpleNamespace(), SimpleNamespace()

    dg.record(first, dg.VISUAL_SUMMARY_FAILURE, "boom")

    assert dg.log_for(first).summary()["total"] == 1
    assert dg.log_for(second).summary()["total"] == 0


def test_record_never_raises_on_a_ctx_that_rejects_attributes():
    """Degradation bookkeeping must not be able to break a run."""

    dg = _module()

    class Frozen:
        __slots__ = ()

    dg.record(Frozen(), dg.TRANSCRIPT_FALLBACK, "boom")  # must not raise


def test_code_identity_names_the_revision_that_ran():
    dg = _module()
    identity = dg.code_identity()

    assert len(identity["head"]) >= 7
    assert isinstance(identity["dirty_files"], int)
    assert isinstance(identity["branch"], str)


def test_environment_carries_backend_summary_degradations_and_code(monkeypatch):
    dg = _module()
    monkeypatch.setattr(
        dg, "hierarchy_backend_summary", lambda _client: {"backend": "helper", "switches": []}
    )
    ctx = SimpleNamespace(ui_adb_client=object())
    dg.record(ctx, dg.TRANSCRIPT_FALLBACK, "API key required")

    environment = dg.build_run_environment(ctx)

    assert environment["backend"] == "helper"
    assert environment["switches"] == []
    assert environment["degradations"]["total"] == 1
    assert environment["degradations"]["capability_degraded"] is True
    assert len(environment["code"]["head"]) >= 7


def test_environment_without_a_backend_summary_still_carries_what_it_knows(monkeypatch):
    dg = _module()
    monkeypatch.setattr(dg, "hierarchy_backend_summary", lambda _client: {})
    ctx = SimpleNamespace(ui_adb_client=None)

    environment = dg.build_run_environment(ctx)

    assert environment["degradations"]["total"] == 0
    assert len(environment["code"]["head"]) >= 7
