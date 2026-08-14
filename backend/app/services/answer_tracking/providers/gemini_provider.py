"""Gemini provider — STUB (Part A).

Registered so it can be enabled via ANSWER_TRACKING_PROVIDERS and configured with a
model/key, but the actual call is not implemented yet. It declares citation support
(Gemini can return grounding) and raises a clear, NON-transient error if invoked, so a
misconfigured run records a real failure row rather than silently retrying forever.
Not in the default provider list. Implement the HTTP call in a later step.
"""
from __future__ import annotations

from .base import BaseProvider, ProviderError, ProviderResult


class GeminiProvider(BaseProvider):
    name = "gemini"
    supports_citations = False      # stub — search/grounding not wired yet, so cannot report

    async def query(self, prompt: str, *, timeout: int, search: bool = False) -> ProviderResult:
        raise ProviderError("gemini provider not implemented", transient=False)
