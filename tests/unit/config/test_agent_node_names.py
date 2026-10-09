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

"""``AgentNode`` is the public list of node names, so every entry must resolve.

``LLMConfig.get_agent`` looked the name up with a bare ``getattr``, and the
``GetLLM`` protocol types ``name`` as this same literal. A name that is declared
there but has no config field therefore type-checks, passes review and CI, and
then raises ``AttributeError`` at runtime. That is how the pixel safety net's old
``name="validator"`` fallback stayed hidden and silently bypassed itself on every
coordinate action.
"""

from typing import Any, cast, get_args

import pytest

from artemis.config.constants import AgentNode
from artemis.config.llm import LLMConfig, LLMWithFallback, get_default_llm_config


def test_every_declared_agent_node_resolves_on_a_real_config():
    cfg = get_default_llm_config()
    failures = {}
    for name in get_args(AgentNode):
        try:
            cfg.get_agent(name)
        except Exception as exc:  # noqa: BLE001 - report whichever way it breaks
            failures[name] = f"{type(exc).__name__}: {exc}"

    assert failures == {}, f"AgentNode advertises names no config can resolve: {failures}"


def test_unknown_node_fails_with_a_self_explanatory_error():
    """A bad name must say what went wrong instead of leaking an AttributeError."""
    cfg = get_default_llm_config()

    with pytest.raises(ValueError) as excinfo:
        cfg.get_agent(cast(Any, "validator"))

    message = str(excinfo.value)
    assert "validator" in message
    assert "planner_validation" in message, "the error must list the nodes that do exist"


def test_unset_optional_node_without_a_default_never_returns_none():
    """``get_agent`` promises an ``LLMWithFallback``.

    The four optional nodes each inherit a default, so the fall-through is
    unreachable today — but a future optional node would silently return None,
    and an absent model is precisely the shape callers mishandle (the pixel safety
    net failed open on one).
    """

    class ConfigWithNewOptionalNode(LLMConfig):
        extra_optional_node: LLMWithFallback | None = None

    cfg = ConfigWithNewOptionalNode(**get_default_llm_config().model_dump())

    with pytest.raises(ValueError):
        cfg.get_agent(cast(Any, "extra_optional_node"))
