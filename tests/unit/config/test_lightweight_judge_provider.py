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

"""The two lightweight judge nodes must run on the configured endpoint.

``validator_pixel_safety_net`` (before every coordinate action) and
``planner_validation`` (after every milestone edit) used to be pinned to a Google
model no matter what the user configured. A run whose every other node used an
OpenAI-compatible endpoint and had no Google credential therefore had a planner
validator that silently failed, and a pixel safety net whose judge could not be
built at all — which the caller swallows as a bypass, so no coordinate action was
ever validated.
"""

import pytest

from artemis.config.llm import (
    LLMConfig,
    _expand_default_into_nodes,
    lightweight_judge_default,
)

JUDGE_NODES = ("planner_validation", "validator_pixel_safety_net")

#: Nodes that are optional in ``LLMConfig`` and therefore never materialised by
#: ``default`` expansion — any pin for them used to be dropped without a warning.
OPTIONAL_NODES = (
    "history_analyzer",
    "output_analyzer",
    "planner_validation",
    "validator_pixel_safety_net",
)


def _config(provider: str = "openai", model: str = "deepseek-flash", **nodes):
    """Build an LLMConfig the way a user's ``artemis.jsonc`` is built."""
    raw = {
        "default": {
            "provider": provider,
            "model": model,
            "fallback": {"provider": provider, "model": model},
        },
        "nodes": dict(nodes),
    }
    return LLMConfig(**_expand_default_into_nodes(raw))


@pytest.mark.parametrize("node", JUDGE_NODES)
def test_judge_nodes_use_the_configured_provider(node):
    """A judge must not require a credential the rest of the run does not use."""
    judge = _config().get_agent(node)

    assert judge.provider == "openai"
    assert judge.model == "deepseek-flash"
    assert judge.fallback.provider == "openai"


@pytest.mark.parametrize("node", JUDGE_NODES)
def test_judge_nodes_stay_deterministic(node):
    """Judges are verdicts, not prose: temperature 0 is part of the contract."""
    assert _config().get_agent(node).temperature == 0.0


def test_judge_nodes_can_be_pinned_in_config():
    """Writing a judge under ``nodes`` must win instead of being ignored."""
    cfg = _config(planner_validation={"model": "deepseek-v4-pro"})

    pinned = cfg.get_agent("planner_validation")
    assert pinned.model == "deepseek-v4-pro"
    assert pinned.provider == "openai"  # inherited from `default`
    # Only the pinned judge changes; its sibling keeps the lightweight default.
    assert cfg.get_agent("validator_pixel_safety_net").model == "deepseek-flash"


@pytest.mark.parametrize("node", OPTIONAL_NODES)
def test_optional_nodes_can_be_pinned_in_config(node):
    """Every optional node must honour an explicit pin under ``nodes``."""
    cfg = _config(**{node: {"model": "deepseek-v4-pro"}})

    assert cfg.get_agent(node).model == "deepseek-v4-pro"


def test_unpinned_optional_nodes_still_get_their_inherited_default():
    """Pinning one optional node must not disturb the other three."""
    cfg = _config(history_analyzer={"model": "deepseek-v4-pro"})

    assert cfg.get_agent("history_analyzer").model == "deepseek-v4-pro"
    # output_analyzer inherits log_analyzer; the judges keep the lightweight default.
    assert cfg.get_agent("output_analyzer").model == "deepseek-flash"
    assert cfg.get_agent("planner_validation").model == "deepseek-flash"


def test_lightweight_judge_default_needs_no_reference_node():
    """Standalone callers keep the historical Google flash-lite default."""
    judge = lightweight_judge_default()

    assert judge.provider == "google"
    assert judge.temperature == 0.0
