"""Perplexity provider (OpenAI-compatible Chat Completions with search).

Perplexity is search-grounded and returns a `citations` list, so this provider CAN
report citations: it maps them to a list (possibly empty when it cited nothing) and
never returns None. The model string comes from config (env) only.
"""
from __future__ import annotations

import time

from .base import BaseProvider, ProviderResult, post_json

_ENDPOINT = "https://api.perplexity.ai/chat/completions"


class PerplexityProvider(BaseProvider):
    name = "perplexity"
    supports_citations = True       # search-grounded -> [] means "cited nothing", never None

    async def query(self, prompt: str, *, timeout: int) -> ProviderResult:
        started = time.perf_counter()
        body = await post_json(
            _ENDPOINT,
            headers={
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        choices = body.get("choices") or []
        text = ""
        if choices:
            text = (choices[0].get("message") or {}).get("content") or ""
        # A citation-capable provider ALWAYS returns a list (never None). Each raw entry
        # is a URL string; title is unknown here and filled by Part B if needed.
        citations = [{"url": u, "title": None} for u in (body.get("citations") or [])]
        usage = body.get("usage") or {}
        tokens = {"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")}
        return ProviderResult(
            text=text,
            citations=citations,
            model=body.get("model") or self.model,
            tokens=tokens,
            latency_ms=latency_ms,
        )
