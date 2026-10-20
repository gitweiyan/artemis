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

"""The rig playbook must not rot: every自动化落点 it cites has to exist.

A knowledge file whose "artifact" column points at a deleted tool is how the last set of
iOS lessons stopped being used (they named `tools/ios_hid_preflight.py`, which was never
created). This test keeps the playbook honest in the cheapest possible way: the files and
smoke stages it claims are checked here, and the canonical file is discoverable from the
repo root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / "docs" / "ios-rig-playbook.md"

#: Paths the playbook promises exist (keep in sync when a lesson is moved).
CITED_PATHS = (
    "artemis/perception/ios_targets.py",
    "artemis/reflex/mouse.py",
    "artemis/reflex/control.py",
    "artemis/drivers/ios/smoke.py",
)


def test_the_playbook_exists_and_is_linked_from_the_repo_root():
    assert PLAYBOOK.is_file()
    agent_notes = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "docs/ios-rig-playbook.md" in agent_notes, "an agent must be pointed at it"
    assert "ios-smoke" in agent_notes, "and at the gate that proves the rig works"


@pytest.mark.parametrize("relative", CITED_PATHS)
def test_every_artifact_the_playbook_cites_exists(relative):
    assert (ROOT / relative).is_file(), f"playbook cites a missing file: {relative}"


def test_the_playbook_smoke_stages_are_the_ones_the_smoke_emits():
    """The playbook lists snapshot / pointer_motion / locate / tap; the code must agree."""
    smoke = (ROOT / "artemis/drivers/ios/smoke.py").read_text(encoding="utf-8")
    playbook = PLAYBOOK.read_text(encoding="utf-8")
    for stage in ("snapshot", "pointer_motion", "locate", "tap"):
        assert f'"{stage}"' in smoke, f"smoke no longer emits the {stage} stage"
        assert stage in playbook, f"playbook no longer documents the {stage} stage"


def test_the_playbook_states_the_promotion_rule():
    """New lessons must land as code/tests/stages; prose-only is recorded as debt."""
    text = PLAYBOOK.read_text(encoding="utf-8")
    assert "prose-only" in text
    assert "唯一事实源" in text, "one canonical source, or it drifts again"
