"""Stub implementations of the paid adapters.

These return clearly-labelled placeholder data so the paid endpoints are wired
end to end and the frontend can render them. Replace with real providers.

INTEGRATION POINTS (see INTEGRATIONS.md):
  - PromptSimulator.run  -> call OpenAI / Anthropic / Google / Perplexity APIs
  - FixGenerator.generate -> call an LLM with a schema-constrained prompt
Set the relevant API keys in .env before implementing.
"""
from __future__ import annotations

from app.services.inference.base import (
    EngineResult, FixGenerator, PromptSimulator,
)


class StubPromptSimulator(PromptSimulator):
    async def run(self, prompt, brand_domain, engines, runs=3):
        # TODO: replace with real per-engine API calls, multiple runs,
        # and a confidence interval. This stub is deterministic placeholder.
        out = []
        for i, e in enumerate(engines):
            out.append(EngineResult(
                engine=e,
                brand_mentioned=(i % 2 == 0),
                domain_cited=(i % 3 == 0),
                position=(i + 1) if i % 2 == 0 else None,
                raw_excerpt="[stub: real excerpt comes from the provider API]",
            ))
        return out


class StubFixGenerator(FixGenerator):
    async def generate(self, asset_type, page_url, page_html):
        # TODO: replace with a real LLM call that reads page_html and emits a
        # valid asset. Return value must be copy-paste ready.
        samples = {
            "organization_jsonld":
                '{\n  "@context": "https://schema.org",\n'
                '  "@type": "Organization",\n  "name": "TODO",\n'
                '  "url": "' + page_url + '"\n}',
            "llms_txt":
                "# llms.txt (stub)\n# TODO: generate from real page content\n"
                "User-agent: *\nAllow: /\n",
            "faq_jsonld":
                '{\n  "@context": "https://schema.org",\n'
                '  "@type": "FAQPage",\n  "mainEntity": []\n}',
        }
        return samples.get(asset_type, f"[stub asset: {asset_type}]")
