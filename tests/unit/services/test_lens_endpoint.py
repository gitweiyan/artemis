"""The provider-aware lens factory (improvement item I1).

The three memory/perception lenses (flash step summarizer, flash runner fallback
VLM, memory capsule compressor) used to build a Google client directly, because
the only endpoint in reach when they were written was Gemini. On a workspace
configured against another endpoint that client cannot even be constructed
("API key required for Gemini Developer API"), so the provider has to come from
the role's config while the *model name* stays the caller's choice.
"""

from __future__ import annotations

from types import SimpleNamespace


from artemis.config.llm import LLM, LLMWithFallback
from artemis.services import llm as llm_service


def _factory():
    """The provider-aware entry, failing cleanly while it does not exist yet."""
    assert hasattr(llm_service, "lens_endpoint"), "provider-aware lens factory is missing"
    return llm_service.lens_endpoint


def _ctx(
    provider: str = "openai", model: str = "deepseek-flash", *, fallback: str = "deepseek-chat"
):
    """A context whose summarizer role resolves to ``provider``/``model``."""
    cfg = LLMWithFallback(
        provider=provider,
        model=model,
        temperature=0.0,
        fallback=LLM(provider=provider, model=fallback, temperature=0.0),
    )
    llm_config = SimpleNamespace(get_utils=lambda _name: cfg, get_agent=lambda _name: cfg)
    return SimpleNamespace(llm_config=llm_config)


def test_lens_endpoint_takes_the_provider_from_config_and_the_model_from_the_caller():
    """The caller knows the model; the config knows the provider that can serve it."""
    endpoint = _factory()(
        _ctx("openai", "deepseek-flash"), "summarizer", "deepseek-v4-pro", is_utils=True
    )

    assert endpoint.provider.value == "openai"
    assert endpoint.model_name == "deepseek-v4-pro"


def test_lens_endpoint_defaults_to_the_configured_model_when_the_caller_names_none():
    endpoint = _factory()(_ctx("openai", "deepseek-flash"), "summarizer", is_utils=True)

    assert endpoint.provider.value == "openai"
    assert endpoint.model_name == "deepseek-flash"


def test_lens_endpoint_keeps_google_when_the_role_is_configured_for_google():
    """The Google path stays available: it is a provider choice, not a hardcode."""
    endpoint = _factory()(
        _ctx("google", "gemini-3.8-flash"), "summarizer", "gemini-2.5-flash-lite", is_utils=True
    )

    assert endpoint.provider.value == "google"
    assert endpoint.model_name == "gemini-2.5-flash-lite"


def test_lens_endpoint_applies_the_callers_temperature_without_touching_the_rest():
    endpoint = _factory()(
        _ctx("openai", "deepseek-flash"),
        "summarizer",
        "deepseek-flash",
        is_utils=True,
        temperature=0.7,
    )

    assert endpoint.temperature == 0.7
    assert endpoint.provider.value == "openai"
