"""Paid-feature adapter interfaces (Step 6).

CRITICAL: nothing here runs in the free scanner. These are the contracts for
the metered, paywalled features. The developer implements the concrete
providers by plugging in API keys. The interfaces are fixed so the rest of the
app does not change when a real provider is dropped in.

Two things every implementation MUST honour:
  1. Budget: each call decrements the caller's plan quota (enforced upstream).
  2. Honesty: prompt simulation via a provider API is labelled as API-measured,
     not consumer-surface measured. Do not present a heuristic as a real citation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EngineResult:
    engine: str            # chatgpt | claude | gemini | perplexity | ai_overviews
    brand_mentioned: bool
    domain_cited: bool
    position: int | None   # rank of the brand in the answer, if mentioned
    raw_excerpt: str        # short, for the UI; respect copyright limits


class PromptSimulator(ABC):
    """Runs one prompt against one or more engines and reports presence."""

    @abstractmethod
    async def run(self, prompt: str, brand_domain: str,
                  engines: list[str], runs: int = 3) -> list[EngineResult]:
        ...


class FixGenerator(ABC):
    """Generates a copy-paste asset (schema, FAQ, llms.txt) from page content."""

    @abstractmethod
    async def generate(self, asset_type: str, page_url: str,
                       page_html: str) -> str:
        ...
