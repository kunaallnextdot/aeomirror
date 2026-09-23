"""AEO Answer Simulator — optional LLM provider interface.

Mirrors services/answer_tracking/providers/base.py's shape deliberately (async
call, ProviderError-style failures) so the two provider families stay consistent to
read — even though the simulator is evidence-grounded, not free-form, and this LLM
step is entirely OPTIONAL (disabled by default; see app/config.py's
answer_simulator_* settings).
"""
from __future__ import annotations

from dataclasses import dataclass

# Re-exported so callers only need to import from this module.
from app.services.answer_tracking.providers.base import ProviderError, post_json  # noqa: F401


@dataclass
class SimulatedAnswer:
    answer_text: str
    brand_mentioned: bool | None
    mention_context: str | None
    missing_information: list[str]
    confidence: str        # "high" | "medium" | "low"
    model: str
    latency_ms: int


class AnswerSimulatorProvider:
    """Subclasses set `name` and implement `generate_answer`."""
    name: str = ""

    async def generate_answer(self, question: str, evidence, *, timeout: int) -> SimulatedAnswer:
        raise NotImplementedError
