"""Provider registry.

Providers self-declare a `name`; `enabled_providers()` reads ANSWER_TRACKING_PROVIDERS
and returns instances for the ones that are BOTH implemented AND fully configured (API
key + model present). Anything listed-but-unconfigured is logged at WARNING and SKIPPED
— the app never crashes and never silently runs with fewer providers than expected.

Model + key + rate all come from settings (env). A provider needs an exact model string
(never a floating alias): an empty model config means "skip", so stored results always
carry the model they were produced with.
"""
from __future__ import annotations

import logging

from app.config import settings

from .base import BaseProvider, ProviderError, ProviderResult, is_transient, post_json
from .anthropic_provider import AnthropicProvider
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider
from .perplexity_provider import PerplexityProvider

log = logging.getLogger("app.answer_tracking.providers")

# name -> class. Gemini is registered (so it is configurable) but stubbed.
_REGISTRY: dict[str, type[BaseProvider]] = {
    AnthropicProvider.name: AnthropicProvider,
    OpenAIProvider.name: OpenAIProvider,
    PerplexityProvider.name: PerplexityProvider,
    GeminiProvider.name: GeminiProvider,
}


def _api_key_for(name: str) -> str | None:
    """The configured API key for a provider (`<name>_api_key` on settings)."""
    return getattr(settings, f"{name}_api_key", None)


def _model_for(name: str) -> str:
    """The configured exact model string (`answer_tracking_model_<name>`)."""
    return (getattr(settings, f"answer_tracking_model_{name}", "") or "").strip()


def rate_for(name: str) -> float:
    """Flat estimated USD per call for cost projection/accumulation."""
    return float(getattr(settings, f"answer_tracking_rate_{name}_usd", 0.0) or 0.0)


def configured_provider_names() -> list[str]:
    """Provider names requested by config, in order (may include unconfigured ones)."""
    return settings.answer_tracking_provider_list()


def extraction_provider() -> BaseProvider | None:
    """The provider used for Part B extraction (ANSWER_TRACKING_EXTRACTION_PROVIDER +
    the resolved extraction model). Returns None (logged) when unknown/unconfigured so
    the caller can mark extraction failed rather than crash."""
    name = (settings.answer_tracking_extraction_provider or "").strip().lower()
    cls = _REGISTRY.get(name)
    if cls is None:
        log.warning("answer-tracking: extraction provider %r is not implemented", name)
        return None
    key = _api_key_for(name)
    if not key:
        log.warning("answer-tracking: extraction provider %r has no API key", name)
        return None
    model = settings.answer_tracking_extraction_model_resolved
    if not model:
        log.warning("answer-tracking: no extraction model configured")
        return None
    return cls(api_key=key, model=model)


def enabled_providers() -> list[BaseProvider]:
    """Instantiate every requested provider that is implemented AND configured. Skips
    (with a WARNING) unknown names, missing API keys, and missing model strings."""
    out: list[BaseProvider] = []
    for name in configured_provider_names():
        cls = _REGISTRY.get(name)
        if cls is None:
            log.warning("answer-tracking: provider %r is not implemented; skipping", name)
            continue
        key = _api_key_for(name)
        if not key:
            log.warning("answer-tracking: provider %r has no API key; skipping", name)
            continue
        model = _model_for(name)
        if not model:
            log.warning("answer-tracking: provider %r has no model configured; skipping", name)
            continue
        out.append(cls(api_key=key, model=model))
    return out


__all__ = [
    "BaseProvider", "ProviderError", "ProviderResult", "is_transient", "post_json",
    "enabled_providers", "extraction_provider", "configured_provider_names", "rate_for",
    "AnthropicProvider", "OpenAIProvider", "PerplexityProvider", "GeminiProvider",
]
