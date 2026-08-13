"""Anthropic provider (Messages API).

Without a web-search tool wired in, Claude does not return web citations, so this
provider reports `citations = None` (CANNOT report) — never an empty list. Grounded
citations are a Part B concern. The model string comes from config (env) only.
"""
from __future__ import annotations

import time

from .base import BaseProvider, ProviderResult, post_json

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 1024


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    supports_citations = False      # no web tool here -> citations reported as None

    async def query(self, prompt: str, *, timeout: int) -> ProviderResult:
        started = time.perf_counter()
        body = await post_json(
            _ENDPOINT,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": _MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        text = "".join(
            block.get("text", "") for block in (body.get("content") or [])
            if block.get("type") == "text"
        )
        usage = body.get("usage") or {}
        tokens = {"input": usage.get("input_tokens"), "output": usage.get("output_tokens")}
        return ProviderResult(
            text=text,
            citations=None,                 # cannot report citations (see supports_citations)
            model=body.get("model") or self.model,
            tokens=tokens,
            latency_ms=latency_ms,
        )
