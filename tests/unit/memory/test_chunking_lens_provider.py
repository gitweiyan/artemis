"""The capsule lens (L2/L3 memory compression) rides the configured provider.

The compressor used a raw ``get_google_llm`` client, so on a workspace without a
Google credential every capsule died and chunk headers stayed pending forever
(improvement item I1 / documented issue S2). The provider now comes from the
summarizer role's config; the model name stays ``chunking.model``, and the
availability fallback must match that provider instead of assuming Google.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from artemis.memory.chunking import StepCapsuleLens

SENTINEL = object()


def _ctx(provider: str = "openai"):
    """A context whose summarizer role uses ``provider``."""
    cfg = SimpleNamespace(
        provider=provider,
        model="deepseek-flash",
    )
    return SimpleNamespace(
        llm_config=SimpleNamespace(
            get_utils=lambda _name: cfg, get_agent=lambda _name: cfg, summarizer=cfg
        ),
        data_engine=None,
    )


def test_capsule_lens_builds_on_the_configured_provider():
    lens = StepCapsuleLens(model_name="deepseek-flash", ctx=_ctx())

    with (
        patch("artemis.services.llm.get_llm_for_model", return_value=SENTINEL) as factory,
        patch("artemis.services.llm.get_google_llm") as google,
    ):
        llm = lens._get_llm()

    assert llm is SENTINEL
    factory.assert_called_once_with(lens._ctx, "summarizer", "deepseek-flash", temperature=0.0)
    assert not google.called, "a non-Google workspace cannot build a Google client at all"


def test_the_capsule_lens_reads_the_role_from_the_agent_namespace():
    """The mock above answers any role name; the real config does not.

    ``summarizer`` is an agent node, so ``is_utils=True`` raised
    ``'LLMConfigUtils' object has no attribute 'summarizer'`` and every capsule
    died — for 18 sessions, silently.
    """
    from artemis.config.llm import get_default_llm_config

    ctx = SimpleNamespace(llm_config=get_default_llm_config(), data_engine=None)
    lens = StepCapsuleLens(model_name="deepseek-flash", ctx=ctx)

    with (
        patch("artemis.services.llm.get_llm_for_model", return_value=SENTINEL) as factory,
        patch("artemis.services.llm.get_google_llm") as google,
    ):
        assert lens._get_llm() is SENTINEL

    assert factory.call_args.args[1] == "summarizer"
    assert not factory.call_args.kwargs.get("is_utils"), "an agent role is not a utils node"
    assert not google.called


def test_a_capsule_lens_that_cannot_be_built_refuses_further_attempts():
    """I4: one unbuildable lens, one verdict — not one probe per attempt."""
    lens = StepCapsuleLens(model_name="deepseek-flash", ctx=_ctx())

    with patch(
        "artemis.services.llm.get_llm_for_model",
        side_effect=RuntimeError("'LLMConfigUtils' object has no attribute 'summarizer'"),
    ):
        assert lens.usable is False
        assert lens.usable is False

    assert lens.build_failure and "summarizer" in lens.build_failure


def test_capsule_lens_without_a_context_keeps_the_legacy_google_path():
    """No ctx means no config to read, so the raw path stays as the documented last resort."""
    lens = StepCapsuleLens(model_name="gemini-3.8-flash")

    with patch("artemis.services.llm.get_google_llm", return_value=SENTINEL) as google:
        llm = lens._get_llm()

    assert llm is SENTINEL
    google.assert_called_once_with(model_name="gemini-3.8-flash", temperature=0.0)


def test_the_capsule_service_stops_retrying_a_lens_it_cannot_build():
    """I4 at the service level: one probe, then no attempt is retried."""
    from artemis.memory.chunking import ChunkCapsuleService

    lens = StepCapsuleLens(model_name="deepseek-flash", ctx=_ctx())
    service = ChunkCapsuleService(ctx=None, lens=lens, retry_limit=3)

    with patch(
        "artemis.services.llm.get_llm_for_model",
        side_effect=RuntimeError("'LLMConfigUtils' object has no attribute 'summarizer'"),
    ):
        assert service.job_can_succeed("chunk:1-2") is False

    assert service.job_can_succeed("chunk:1-2") is False, "the verdict is cached"


def test_the_capsule_service_keeps_retrying_a_lens_that_builds():
    from artemis.memory.chunking import ChunkCapsuleService

    lens = StepCapsuleLens(model_name="deepseek-flash", ctx=_ctx())
    service = ChunkCapsuleService(ctx=None, lens=lens, retry_limit=3)

    with patch("artemis.services.llm.get_llm_for_model", return_value=SENTINEL):
        assert service.job_can_succeed("chunk:1-2") is True
