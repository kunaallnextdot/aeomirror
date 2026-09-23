"""AEO Answer Simulator — optional, low-cost external-LLM fallback.

Wraps the existing, already-vetted app/core/ai.py::complete()/parse_json() rather
than a new HTTP client — this is the practical answer to "production likely has no
GPU": an operator sets ANSWER_SIMULATOR_PROVIDER=anthropic to pay the same small
per-call Claude cost ai.py already uses elsewhere for the report narrative, or
leaves the simulator disabled entirely for $0.
"""
from __future__ import annotations

import time

from app.config import settings
from app.core import ai
from app.services.answer_simulator.prompting import build_facts_payload, build_prompt, validate_against_facts
from app.services.answer_simulator.providers.base import AnswerSimulatorProvider, ProviderError, SimulatedAnswer


class AnthropicSimulatorProvider(AnswerSimulatorProvider):
    name = "anthropic"

    async def generate_answer(self, question: str, evidence, *, timeout: int) -> SimulatedAnswer:
        facts = build_facts_payload(question, evidence)
        system, user_content = build_prompt(facts)
        started = time.monotonic()
        raw = ai.complete(system, user_content, timeout=timeout)
        latency_ms = int((time.monotonic() - started) * 1000)

        if raw is None:
            raise ProviderError("Anthropic simulator call failed or is not configured")

        parsed = ai.parse_json(raw)
        validated = validate_against_facts(parsed, facts)
        if validated is None:
            raise ProviderError("Anthropic simulator response failed FACTS validation")

        return SimulatedAnswer(
            answer_text=validated["answer_text"],
            brand_mentioned=validated["brand_mentioned"],
            mention_context=validated["mention_context"],
            missing_information=validated["missing_information"],
            confidence=validated["confidence"],
            model=settings.ai_model,
            latency_ms=latency_ms,
        )
