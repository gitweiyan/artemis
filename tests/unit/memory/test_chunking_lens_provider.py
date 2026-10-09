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
    factory.assert_called_once_with(
        lens._ctx, "summarizer", "deepseek-flash", is_utils=True, temperature=0.0
    )
    assert not google.called, "a non-Google workspace cannot build a Google client at all"


def test_capsule_lens_without_a_context_keeps_the_legacy_google_path():
    """No ctx means no config to read, so the raw path stays as the documented last resort."""
    lens = StepCapsuleLens(model_name="gemini-3.8-flash")

    with patch("artemis.services.llm.get_google_llm", return_value=SENTINEL) as google:
        llm = lens._get_llm()

    assert llm is SENTINEL
    google.assert_called_once_with(model_name="gemini-3.8-flash", temperature=0.0)
