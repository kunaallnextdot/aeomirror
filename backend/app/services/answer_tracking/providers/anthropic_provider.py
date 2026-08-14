"""Anthropic provider (Messages API), with optional server-side web search.

When `search=True` the request carries the web search tool (verified against
docs.claude.com → web-search-tool), so Claude searches the live web and the response
comes back as MULTIPLE content blocks: `text`, `server_tool_use`, `web_search_tool_result`,
and `text` blocks that carry a `citations` array of `web_search_result_location`
({url, title, cited_text, ...}). We iterate ALL blocks: text from every `text` block, and
cited URLs/titles from every text block's `citations`. `search=True` => citations is a list
(possibly empty = searched, cited nothing); `search=False` is a plain completion (citations
= None, i.e. cannot report). The tool version + model come from config (env) only.
"""
from __future__ import annotations

import time

from app.config import settings

from .base import BaseProvider, ProviderResult, post_json

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 1024


def parse_web_search_citations(content: list) -> list[dict]:
    """Cited {url, title} pairs from a Messages response's content blocks — the `citations`
    array (type web_search_result_location) attached to text blocks. Deduped by url,
    first-seen order preserved."""
    out, seen = [], set()
    for block in content or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        for c in (block.get("citations") or []):
            if not isinstance(c, dict):
                continue
            url = c.get("url")
            if url and url not in seen:
                seen.add(url)
                out.append({"url": url, "title": c.get("title")})
    return out


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    supports_citations = True        # web search wired -> can report citations

    async def query(self, prompt: str, *, timeout: int, search: bool = False) -> ProviderResult:
        started = time.perf_counter()
        body = {
            "model": self.model,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        if search:
            body["tools"] = [{"type": settings.answer_tracking_anthropic_search_tool,
                              "name": "web_search"}]
        resp = await post_json(
            _ENDPOINT,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        content = resp.get("content") or []
        text = "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
        usage = resp.get("usage") or {}
        tokens = {"input": usage.get("input_tokens"), "output": usage.get("output_tokens")}
        # search on -> report citations (list, maybe empty); search off -> cannot report (None)
        citations = parse_web_search_citations(content) if search else None
        return ProviderResult(
            text=text,
            citations=citations,
            model=resp.get("model") or self.model,
            tokens=tokens,
            latency_ms=latency_ms,
        )
