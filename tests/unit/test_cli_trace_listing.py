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

"""Step visibility in ``artemis trace list`` / ``view`` (I7).

Both 2026-10-09 archives contained ``steps.json`` and both were empty, so the
"📋 Steps" chip asserted the opposite of the truth, and ``view`` printed an
empty table instead of naming what it could read.
"""

import json

from typer.testing import CliRunner

from artemis.interfaces.cli.main import app

runner = CliRunner()


def _session(tmp_path, name="20260101_000000_task", manifest=None):
    session = tmp_path / name
    session.mkdir()
    (session / "recording.mp4").write_bytes(b"fake-video")
    if manifest is not None:
        (session / "steps.json").write_text(json.dumps(manifest), encoding="utf-8")
    return session


def test_empty_manifest_is_not_advertised_as_steps(tmp_path):
    _session(tmp_path, manifest=[])
    result = runner.invoke(app, ["trace", "list", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "🎬 Video" in result.output
    assert "📋 Steps" not in result.output


def test_populated_manifest_is_advertised_as_steps(tmp_path):
    _session(tmp_path, manifest=[{"step": 1, "action": "click", "motivation": "go"}])
    result = runner.invoke(app, ["trace", "list", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "📋 Steps" in result.output


def test_unparseable_manifest_is_not_advertised_as_steps(tmp_path):
    session = _session(tmp_path)
    (session / "steps.json").write_text("{not json", encoding="utf-8")
    result = runner.invoke(app, ["trace", "list", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "📋 Steps" not in result.output


def test_view_renders_the_exported_manifest_schema(tmp_path):
    """The exporter's rows must be exactly what ``trace view`` reads back."""
    _session(
        tmp_path,
        manifest=[
            {
                "step": 1,
                "action": "click(element=搜索)",
                "motivation": "输入关键词",
                "timestamp": 1.0,
            },
            {
                "step": 2,
                "action": "click(element=大鹏)",
                "motivation": "进入结果",
                "timestamp": 2.0,
            },
        ],
    )
    result = runner.invoke(app, ["trace", "view", "20260101_000000_task", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "click(element=搜索)" in result.output
    assert "输入关键词" in result.output
    assert "Session files in" not in result.output


def test_view_falls_back_to_file_listing_without_steps(tmp_path):
    _session(tmp_path, manifest=[])
    result = runner.invoke(app, ["trace", "view", "20260101_000000_task", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Session files in" in result.output
    assert "recording.mp4" in result.output
