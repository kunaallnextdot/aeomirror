"""OpenAI provider (Chat Completions).

Plain chat completions do not return citations, so this provider reports
`citations = None` (CANNOT report) — not an empty list. The model string comes from
config (env) only.
"""
from __future__ import annotations

import time

from .base import BaseProvider, ProviderResult, post_json

_ENDPOINT = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(BaseProvider):
    name = "openai"
    supports_citations = False      # chat completions return no citations -> None

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
        usage = body.get("usage") or {}
        tokens = {"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")}
        return ProviderResult(
            text=text,
            citations=None,                 # cannot report citations (see supports_citations)
            model=body.get("model") or self.model,
            tokens=tokens,
            latency_ms=latency_ms,
        )
