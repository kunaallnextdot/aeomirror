"""Simulator LLM provider registry. `simulator_provider()` returns None (the
deterministic-only default) whenever the optional LLM step is disabled or
misconfigured — never crashes, mirroring services/answer_tracking/providers'
enabled_providers() posture."""
from __future__ import annotations

import logging

from app.config import settings
from app.services.answer_simulator.providers.anthropic_simulator_provider import AnthropicSimulatorProvider
from app.services.answer_simulator.providers.base import AnswerSimulatorProvider, SimulatedAnswer  # noqa: F401
from app.services.answer_simulator.providers.local_llm_provider import LocalLLMProvider

log = logging.getLogger("app.answer_simulator.providers")


def simulator_provider() -> AnswerSimulatorProvider | None:
    if not settings.answer_simulator_enabled:
        return None
    kind = (settings.answer_simulator_provider or "").strip().lower()
    if kind == "local":
        return LocalLLMProvider(base_url=settings.answer_simulator_base_url,
                                model=settings.answer_simulator_model)
    if kind == "anthropic":
        return AnthropicSimulatorProvider()
    if kind and kind != "none":
        log.warning("answer_simulator_provider=%r not recognized; LLM step disabled", kind)
    return None
