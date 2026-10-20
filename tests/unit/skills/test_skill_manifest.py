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

"""A skill manifest is validated in one place, with messages that say how to fix it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from artemis.skills.manifest import SkillManifestError, read_manifest

GOOD = {
    "name": "tetris",
    "version": "0.1.0",
    "kind": "reflex",
    "entry": {
        "perception": "perception:parse",
        "policy": "policy:decide",
        "control": "control:to_gestures",
    },
    "calibration": "calibration.json",
    "plan_hint": "play tetris",
    "requires": {"screenshot": True, "api_key_env": "TYPESAFE_API_KEY"},
    "hooks": {"on_start": "session:start"},
}


def _write(tmp_path: Path, payload: dict | str, name: str = "tetris") -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "skill.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )
    return skill_dir


def test_a_good_manifest_reads_into_a_typed_value(tmp_path):
    manifest = read_manifest(_write(tmp_path, GOOD))

    assert manifest.name == "tetris"
    assert manifest.kind == "reflex"
    assert manifest.entry_for("policy") == "policy:decide"
    assert manifest.entry_for("arena") is None
    assert manifest.calibration_name == "calibration.json"
    assert manifest.plan_hint == "play tetris"
    assert manifest.requires.api_key_env == "TYPESAFE_API_KEY"
    assert manifest.hooks == {"on_start": "session:start"}


def test_the_directory_name_is_the_skill_name(tmp_path):
    with pytest.raises(SkillManifestError) as excinfo:
        read_manifest(_write(tmp_path, {**GOOD, "name": "not-tetris"}))

    assert "directory name is the skill name" in str(excinfo.value)


@pytest.mark.parametrize(
    "payload,needle",
    [
        ({**GOOD, "entry": {}}, "entry.perception"),
        ({**GOOD, "kind": "hologram"}, "kind"),
        ({**GOOD, "requires": {"capabilities": [1, 2]}}, "capabilities"),
        ({**GOOD, "hooks": []}, "hooks"),
        ("not json", "not valid JSON"),
    ],
)
def test_a_bad_manifest_names_the_problem_and_the_file(tmp_path, payload, needle):
    with pytest.raises(SkillManifestError) as excinfo:
        read_manifest(_write(tmp_path, payload))

    assert needle in str(excinfo.value)
    assert "skill.json" in str(excinfo.value)


def test_a_missing_manifest_says_so(tmp_path):
    empty = tmp_path / "tetris"
    empty.mkdir()

    with pytest.raises(SkillManifestError) as excinfo:
        read_manifest(empty)

    assert "is missing" in str(excinfo.value)


def test_a_declared_api_key_reports_when_it_is_unset(monkeypatch, tmp_path):
    manifest = read_manifest(_write(tmp_path, GOOD))

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert manifest.requires.missing_api_key() == "TYPESAFE_API_KEY"

    monkeypatch.setenv("TYPESAFE_API_KEY", "secret")
    assert manifest.requires.missing_api_key() is None


def test_the_real_tetris_manifest_still_validates():
    from artemis.config import ROOT_DIR

    manifest = read_manifest(Path(ROOT_DIR) / "skills" / "tetris")

    assert manifest.name == "tetris"
    assert manifest.kind == "reflex"
    assert manifest.entry_for("arena") == "arena:run_tournament"
