"""AEO Answer Simulator — local, Ollama-compatible LLM provider.

Dev/self-hosted only in practice (Render's production instance is not assumed to
have a GPU — see app/config.py's answer_simulator_* docstring). Any failure
(connection refused when no local server is running, timeout, bad JSON) raises
ProviderError, which the batch orchestrator treats as "LLM step unavailable" and
falls back to the deterministic, evidence-only answer — never a hard run failure.
"""
from __future__ import annotations

import time

from app.services.answer_simulator.prompting import build_facts_payload, build_prompt, validate_against_facts
from app.services.answer_simulator.providers.base import AnswerSimulatorProvider, ProviderError, SimulatedAnswer
from app.services.answer_tracking.providers.base import post_json


class LocalLLMProvider(AnswerSimulatorProvider):
    name = "local_llm"

    def __init__(self, *, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def generate_answer(self, question: str, evidence, *, timeout: int) -> SimulatedAnswer:
        facts = build_facts_payload(question, evidence)
        system, user_content = build_prompt(facts)
        started = time.monotonic()
        body = await post_json(
            f"{self.base_url}/api/generate",
            headers={"Content-Type": "application/json"},
            json={
                "model": self.model,
                "system": system,
                "prompt": user_content,
                "format": "json",
                "stream": False,
            },
            timeout=timeout,
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        raw = body.get("response") if isinstance(body, dict) else None
        parsed = _parse_json(raw)
        validated = validate_against_facts(parsed, facts)
        if validated is None:
            raise ProviderError("local LLM response failed FACTS validation")

        return SimulatedAnswer(
            answer_text=validated["answer_text"],
            brand_mentioned=validated["brand_mentioned"],
            mention_context=validated["mention_context"],
            missing_information=validated["missing_information"],
            confidence=validated["confidence"],
            model=self.model,
            latency_ms=latency_ms,
        )


def _parse_json(raw: str | None) -> dict | None:
    import json
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None
