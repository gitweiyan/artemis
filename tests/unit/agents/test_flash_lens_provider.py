"""The flash lenses ride the provider the config names (improvement item I1).

Both flash lenses used to build a Google client by hand: the step summarizer
always (F1 - it killed Flash during construction with "API key required for
Gemini Developer API"), and the runner's last-resort VLM with a hardcoded
``gemini-2.5-flash``. A workspace configured against another endpoint cannot
construct either, so the provider must come from the role's config while the
model name stays the caller's choice - and a lens that cannot be built must
degrade the run instead of ending it.

The assertions intercept ``ModelFactory``, the single place a chat client is
built, rather than a specific import name: that is the behaviour being fixed
("never hand-build a client"), and it stays honest if the imports change.
"""

from __future__ import annotations

import logging
from unittest.mock import Mock, patch

import pytest

from artemis.agents.flash.runner import FlashRunner
from artemis.agents.flash.summarizer import VisualStepSummarizer
from artemis.context import ArtemisContext
from artemis.llm.router import ModelFactory

SENTINEL = object()


@pytest.fixture
def ctx():
    context = Mock(spec=ArtemisContext)
    context.data_engine = None
    return context


# --------------------------------------------------------------------------- #
# Step summarizer
# --------------------------------------------------------------------------- #


def test_step_summarizer_builds_its_lens_on_the_configured_provider(ctx):
    """An explicit model name must not drag the provider back to Google."""
    with (
        patch("artemis.agents.flash.summarizer.get_llm_for_model", return_value=SENTINEL) as build,
        patch.object(ModelFactory, "create_model") as client,
        patch("artemis.agents.flash.summarizer.get_llm") as gateway,
    ):
        summarizer = VisualStepSummarizer(ctx, model_name="deepseek-flash")

    assert summarizer._llm is SENTINEL
    build.assert_called_once_with(ctx, "summarizer", "deepseek-flash", temperature=0.0)
    assert not client.called, "the lens must not hand-build a chat client"
    assert not gateway.called


def test_step_summarizer_degrades_instead_of_dying_when_the_lens_cannot_be_built(ctx, caplog):
    """F1's other half: a lens that cannot be built must not abort the run."""
    with (
        patch(
            "artemis.agents.flash.summarizer.get_llm_for_model",
            side_effect=RuntimeError("no endpoint for this role"),
        ),
        patch.object(ModelFactory, "create_model") as client,
        caplog.at_level(logging.WARNING),
    ):
        summarizer = VisualStepSummarizer(ctx, model_name="deepseek-flash")

    assert summarizer._llm is None
    assert summarizer._model_name == "deepseek-flash"  # the run still reports what it asked for
    assert not client.called, "a Google client is not a rescue in a non-Google workspace"
    assert "no endpoint for this role" in caplog.text


def test_step_summarizer_without_an_explicit_model_uses_the_role_config(ctx):
    with (
        patch("artemis.agents.flash.summarizer.get_llm", return_value=SENTINEL) as gateway,
        patch.object(ModelFactory, "create_model") as client,
    ):
        summarizer = VisualStepSummarizer(ctx)

    assert summarizer._llm is SENTINEL
    gateway.assert_called_once_with(ctx, name="summarizer")
    assert not client.called


def test_a_step_summarizer_without_a_lens_refuses_further_attempts(ctx):
    """I4: the retry budget is for transient failures, not for a missing lens."""
    with patch(
        "artemis.agents.flash.summarizer.get_llm_for_model",
        side_effect=RuntimeError("no endpoint for this role"),
    ):
        summarizer = VisualStepSummarizer(ctx, model_name="deepseek-flash")

    assert summarizer._llm is None
    assert summarizer.job_can_succeed("step:1") is False


def test_a_step_summarizer_with_a_lens_keeps_its_retries(ctx):
    with patch("artemis.agents.flash.summarizer.get_llm_for_model", return_value=SENTINEL):
        summarizer = VisualStepSummarizer(ctx, model_name="deepseek-flash")

    assert summarizer.job_can_succeed("step:1") is True


# --------------------------------------------------------------------------- #
# FlashRunner: lens construction and its own LLM fallback
# --------------------------------------------------------------------------- #


def test_flash_runner_survives_a_step_summarizer_that_cannot_be_built(ctx, caplog):
    with (
        patch("artemis.controllers.unified_controller.get_driver"),
        patch(
            "artemis.agents.flash.runner.VisualStepSummarizer",
            side_effect=RuntimeError("lens unavailable"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        runner = FlashRunner(ctx, goal="Test Open Settings")

    assert runner.summarizer is None
    assert "lens unavailable" in caplog.text


def test_flash_runner_falls_back_to_the_configured_node_not_google(ctx):
    calls: list[dict] = []

    def fake_get_llm(_ctx, name, **kwargs):
        calls.append({"name": name, **kwargs})
        if len(calls) == 1:
            raise ValueError("operator role is misconfigured")
        return SENTINEL

    with (
        patch("artemis.controllers.unified_controller.get_driver"),
        patch("artemis.agents.flash.runner.get_llm", side_effect=fake_get_llm),
        # The step summarizer builds its own lens on the configured provider, and
        # since I1 it succeeds (it used to fail on the real config, which is why
        # this assertion looked green). Keep this test on the runner's fallback.
        patch("artemis.agents.flash.summarizer.get_llm_for_model", return_value=SENTINEL),
        patch("artemis.agents.flash.summarizer.get_llm", return_value=SENTINEL),
        patch.object(ModelFactory, "create_model") as client,
    ):
        runner = FlashRunner(ctx, goal="g")
        llm = runner._init_llm()

    assert llm is SENTINEL
    assert [c["name"] for c in calls] == ["operator", "operator"]
    assert calls[1].get("use_fallback") is True
    assert not client.called, "a Google client is not a fallback in a non-Google workspace"


def test_flash_runner_reports_an_unusable_operator_endpoint(ctx):
    with (
        patch("artemis.controllers.unified_controller.get_driver"),
        patch(
            "artemis.agents.flash.runner.get_llm",
            side_effect=ValueError("operator role is misconfigured"),
        ),
        patch("artemis.agents.flash.summarizer.get_llm_for_model", return_value=SENTINEL),
        patch("artemis.agents.flash.summarizer.get_llm", return_value=SENTINEL),
        patch.object(ModelFactory, "create_model") as client,
    ):
        runner = FlashRunner(ctx, goal="g")
        with pytest.raises(ValueError):
            runner._init_llm()

    assert not client.called


# --------------------------------------------------------------------------- #
# I2: the capability state of the run is recorded where a reader looks
# --------------------------------------------------------------------------- #


def test_an_unbuildable_step_summarizer_is_recorded_as_a_missing_capability(ctx):
    from artemis.runtime import degradations, run_capabilities

    with patch(
        "artemis.agents.flash.summarizer.get_llm_for_model",
        side_effect=RuntimeError("no endpoint for this role"),
    ):
        VisualStepSummarizer(ctx, model_name="deepseek-flash")

    assert run_capabilities.unavailable_roles(ctx) == ["step_summarizer"]
    summary = degradations.log_for(ctx).summary()
    assert summary["counts"][degradations.LENS_UNAVAILABLE] == 1
    assert summary["capability_degraded"] is True


def test_a_built_step_summarizer_is_recorded_as_available(ctx):
    from artemis.runtime import degradations, run_capabilities

    with patch("artemis.agents.flash.summarizer.get_llm_for_model", return_value=SENTINEL):
        VisualStepSummarizer(ctx, model_name="deepseek-flash")

    lenses = run_capabilities.capabilities(ctx)["lenses"]
    assert lenses["step_summarizer"]["available"] is True
    assert degradations.log_for(ctx).summary()["counts"] == {}
