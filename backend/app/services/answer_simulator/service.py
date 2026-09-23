"""AEO Answer Simulator — run creation + cost estimate.

Deliberately a SEPARATE, small module from services/answer_tracking/service.py
rather than a flag threaded through its security-sensitive `create_run` — a
simulation run costs $0 in deterministic mode (and at most a few cents when the
optional LLM step is requested against the Anthropic fallback), so it does not need
the 72h dedup interval or 8-per-month cap that exist specifically because live
provider calls cost real, uncapped money.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromptRun, PromptSet
from app.services.answer_tracking.providers import rate_for


def create_simulation_run(db: Session, prompt_set: PromptSet, *, llm_step_requested: bool) -> PromptRun:
    run = PromptRun(
        organization_id=prompt_set.organization_id, prompt_set_id=prompt_set.id,
        monitor_id=prompt_set.monitor_id, run_mode="simulator",
        llm_step_requested=llm_step_requested,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def estimate_simulation(question_count: int, *, request_llm_step: bool) -> dict:
    """Projected call count + estimated USD for a batch of `question_count`
    questions. $0 unless the LLM step is requested AND resolves to the paid
    Anthropic fallback (a local/Ollama step is always $0; a disabled/unconfigured
    step is always $0)."""
    from app.services.answer_simulator import providers as sim_providers

    cost_per_call = 0.0
    if request_llm_step:
        provider = sim_providers.simulator_provider()
        if provider is not None and provider.name == "anthropic":
            cost_per_call = rate_for("anthropic")

    return {
        "call_count": question_count,
        "llm_step_requested": request_llm_step,
        "estimated_cost_usd": round(cost_per_call * question_count, 6),
    }
