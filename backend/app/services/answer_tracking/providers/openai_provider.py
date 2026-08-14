"""OpenAI provider (Responses API), with optional server-side web search.

Uses the Responses API (/v1/responses) so the `web_search` tool works with general models
(Chat Completions only searches via special *-search models). When `search=True` we pass
`tools: [{"type": "web_search"}]`; the response's output text carries `annotations` of
`type: "url_citation"` ({url, title, start_index, end_index}). `search=True` => citations is
a list (possibly empty); `search=False` is a plain completion (citations = None). Model comes
from config (env) only.
"""
from __future__ import annotations

import time

from .base import BaseProvider, ProviderResult, post_json

_ENDPOINT = "https://api.openai.com/v1/responses"


def _text_and_citations(resp: dict) -> tuple[str, list[dict]]:
    """Concatenated output text + deduped url_citation {url, title} from a Responses body."""
    text_parts, cites, seen = [], [], set()
    for item in (resp.get("output") or []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in (item.get("content") or []):
            if not isinstance(block, dict) or block.get("type") != "output_text":
                continue
            text_parts.append(block.get("text") or "")
            for a in (block.get("annotations") or []):
                if isinstance(a, dict) and a.get("type") == "url_citation":
                    url = a.get("url")
                    if url and url not in seen:
                        seen.add(url)
                        cites.append({"url": url, "title": a.get("title")})
    return "".join(text_parts), cites


class OpenAIProvider(BaseProvider):
    name = "openai"
    supports_citations = True        # web search wired via the Responses API

    async def query(self, prompt: str, *, timeout: int, search: bool = False) -> ProviderResult:
        started = time.perf_counter()
        body = {"model": self.model, "input": prompt}
        if search:
            body["tools"] = [{"type": "web_search"}]
        resp = await post_json(
            _ENDPOINT,
            headers={
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        text, cites = _text_and_citations(resp)
        usage = resp.get("usage") or {}
        tokens = {"input": usage.get("input_tokens"), "output": usage.get("output_tokens")}
        return ProviderResult(
            text=text,
            citations=(cites if search else None),   # None = cannot report (no search)
            model=resp.get("model") or self.model,
            tokens=tokens,
            latency_ms=latency_ms,
        )
