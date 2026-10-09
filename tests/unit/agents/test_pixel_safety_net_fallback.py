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

"""The pixel safety net's fallback judge must name a node that exists.

``_init_llm_and_prompt`` fell back to ``name="validator"``, which is not a field
of ``LLMConfig``. When the primary judge was unavailable the fallback raised
``AttributeError`` instead of recovering, the exception propagated to
``validate_action_precondition_pixel``, and that returns
``PIXEL_BYPASSED`` — i.e. *allowed*. Every coordinate-target action then ran
unvalidated while the log claimed only that the check was bypassed.
"""

from unittest.mock import MagicMock

import pytest

from artemis.agents.validator.precondition_pixel import _init_llm_and_prompt
from artemis.config.llm import get_default_llm_config


def test_falls_back_to_a_configured_node_when_the_judge_is_unavailable():
    sentinel = object()
    requested: list[str] = []

    def fake_get_llm(_ctx, name):
        requested.append(name)
        if name == "validator_pixel_safety_net":
            raise RuntimeError("judge not configured")
        return sentinel

    llm, prompt = _init_llm_and_prompt(MagicMock(), fake_get_llm)

    assert llm is sentinel
    assert requested == ["validator_pixel_safety_net", "operator"]
    assert prompt.strip(), "the prompt template must still be loaded"


def test_the_fallback_name_resolves_in_the_real_config():
    """Guards the class of bug: a fallback name that no config can resolve."""
    cfg = get_default_llm_config()

    assert cfg.get_agent("operator") is not None
    # 'validator' is the name that used to be used, and it has no config field.
    with pytest.raises(AttributeError):
        cfg.get_agent("validator")
