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

"""I2 · a run records which lenses it could actually use.

Measured on 2026-10-10: every run since 2026-10-09 17:11 lost both the visual step
summarizer and the step-capsule lens (a role resolved in the wrong namespace), and
the only trace was one WARNING per run — 18 sessions, no artifact said a word.
Flash writes no verdict at all, so its capability state has to live beside the
run's identity, where a reader looks.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SESSION_ID = "11111111-2222-3333-4444-555555555555"


def _module():
    """The capability recorder, failing cleanly while it does not exist yet."""
    try:
        return importlib.import_module("artemis.runtime.run_capabilities")
    except ImportError as exc:
        raise AssertionError(f"the capability recorder is missing: {exc}") from exc


def _ctx(tmp_path) -> SimpleNamespace:
    base = tmp_path / "traces" / SESSION_ID
    base.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(data_engine=SimpleNamespace(base_dir=base))


def _payload(ctx) -> dict:
    path = ctx.data_engine.base_dir / _module().CAPABILITIES_FILENAME
    return json.loads(path.read_text(encoding="utf-8"))


def test_an_unavailable_lens_is_recorded_beside_the_run(tmp_path):
    module = _module()
    ctx = _ctx(tmp_path)

    module.record_lens(ctx, "step_summarizer", available=False, reason="role not in utils")

    lens = _payload(ctx)["lenses"]["step_summarizer"]
    assert lens["available"] is False
    assert "role not in utils" in lens["reason"]
    assert module.unavailable_roles(ctx) == ["step_summarizer"]


def test_an_unavailable_lens_is_also_a_counted_degradation(tmp_path):
    """The file is for a reader; the counter is for the verdict (Pro's)."""
    from artemis.runtime import degradations

    module = _module()
    ctx = _ctx(tmp_path)

    module.record_lens(ctx, "step_capsule", available=False, reason="lens could not be built")

    summary = degradations.log_for(ctx).summary()
    assert summary["counts"].get(degradations.LENS_UNAVAILABLE) == 1
    assert summary["capability_degraded"] is True


def test_an_available_lens_is_recorded_without_a_degradation(tmp_path):
    from artemis.runtime import degradations

    module = _module()
    ctx = _ctx(tmp_path)

    module.record_lens(ctx, "step_summarizer", available=True)

    lens = _payload(ctx)["lenses"]["step_summarizer"]
    assert lens["available"] is True
    assert lens["reason"] is None
    assert degradations.log_for(ctx).summary()["counts"] == {}


def test_the_file_is_written_even_when_no_lens_reported_anything(tmp_path):
    """A missing file and "every lens was fine" must not look the same."""
    module = _module()
    ctx = _ctx(tmp_path)

    assert module.write_capabilities(ctx) is True

    assert _payload(ctx)["lenses"] == {}


def test_a_session_path_that_is_not_a_path_writes_nothing(tmp_path, monkeypatch):
    """Mocked contexts satisfy ``os.PathLike``; only a real session dir is written."""
    module = _module()
    monkeypatch.chdir(tmp_path)

    module.record_lens(
        SimpleNamespace(data_engine=MagicMock()),
        "step_summarizer",
        available=False,
        reason="x",
    )

    assert list(tmp_path.iterdir()) == []


def test_recording_a_lens_without_a_name_is_a_programming_error(tmp_path):
    """I/O failures stay quiet; a caller that forgot the role name must not."""
    module = _module()

    with pytest.raises(ValueError):
        module.record_lens(_ctx(tmp_path), "", available=True)
