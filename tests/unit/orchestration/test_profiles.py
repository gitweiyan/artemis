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

"""Profile selection is an orchestration decision, so it is a function with a reason."""

from __future__ import annotations

import pytest

from artemis.orchestration.profiles import (
    ProfileSelectionError,
    select_profile,
)


class Explorer:
    """Stands in for ExplorerConfig: records what it was asked."""

    def __init__(self, by_profile):
        self.by_profile = by_profile
        self.asked: list[tuple[str | None, str | None]] = []

    def resolve(
        self, explicit_version=None, agent_name=None, profile=None, per_agent_overrides=None
    ):
        self.asked.append((explicit_version, profile))
        return explicit_version or self.by_profile.get(profile, "flash")


def test_the_default_profile_is_the_pro_graph():
    plan = select_profile(None, explorer=Explorer({"pro": "pro"}))

    assert plan.kind == "pro"
    assert plan.requested is False
    assert "no profile was requested" in plan.reason


def test_a_requested_profile_is_normalized_and_explained():
    plan = select_profile("  FLASH ", explorer=Explorer({"flash": "flash"}))

    assert plan.kind == "flash"
    assert plan.requested is True
    assert "flash" in plan.reason
    assert plan.explorer_tier == "flash"


def test_the_tier_comes_from_the_config_for_that_profile():
    explorer = Explorer({"flash": "ultra", "pro": "pro"})

    assert select_profile("flash", explorer=explorer).explorer_tier == "ultra"
    assert select_profile("pro", explorer=explorer).explorer_tier == "pro"
    assert explorer.asked == [(None, "flash"), (None, "pro")]


def test_an_explicit_tier_is_forwarded_and_reported():
    plan = select_profile("flash", explorer=Explorer({}), explicit_tier="ultra")

    assert plan.explorer_tier == "ultra"
    assert "pinned" in plan.reason


def test_an_unknown_profile_names_the_valid_ones():
    with pytest.raises(ProfileSelectionError) as excinfo:
        select_profile("turbo")

    message = str(excinfo.value)
    assert "turbo" in message
    for kind in ("flash", "pro", "skill"):
        assert kind in message


def test_the_shipped_defaults_are_used_when_no_config_is_passed():
    plan = select_profile("pro")

    assert plan.kind == "pro"
    assert plan.explorer_tier in ("flash", "pro", "ultra")


def test_the_skill_profile_is_a_valid_choice():
    plan = select_profile("skill", explorer=Explorer({"skill": "flash"}))

    assert plan.kind == "skill"
    assert plan.requested is True
