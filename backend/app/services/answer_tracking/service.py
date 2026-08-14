"""Service layer for answer tracking: ownership lookups, the pre-run cost estimate,
the cost guards (dedup interval + monthly run limit), and the scheduling due-logic.

Cost guards here are OPERATIONAL limits driven by config — NOT plan/tier gating. No
billing integration; the limits are simply configuration (see the feature summary).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    RUN_COMPLETED, RUN_PARTIAL,
    PromptRun, PromptSet, TrackedPrompt,
)

from . import providers as provider_registry
from .errors import RunRefused

log = logging.getLogger("app.answer_tracking.service")

# Runs whose cost has already been spent — used for dedup + cadence anchoring.
_CONSUMING_STATUSES = (RUN_COMPLETED, RUN_PARTIAL)


# --------------------------- ownership ---------------------------
def owned_prompt_set(db: Session, org_id: str, prompt_set_id: str) -> PromptSet | None:
    ps = db.get(PromptSet, prompt_set_id)
    if not ps or ps.organization_id != org_id:
        return None
    return ps


def active_prompts(db: Session, prompt_set_id: str) -> list[TrackedPrompt]:
    return (db.query(TrackedPrompt)
            .filter(TrackedPrompt.prompt_set_id == prompt_set_id,
                    TrackedPrompt.is_active.is_(True))
            .order_by(TrackedPrompt.created_at)
            .all())[:settings.answer_tracking_max_prompts]


def prompt_count(db: Session, prompt_set_id: str) -> int:
    """Total prompts in a set (active + inactive) — the hard cap counts every prompt."""
    return (db.query(TrackedPrompt)
            .filter(TrackedPrompt.prompt_set_id == prompt_set_id).count())


# --------------------------- estimate ---------------------------
def estimate_run(db: Session, prompt_set: PromptSet) -> dict:
    """Projected call count + estimated USD for ONE run of this set. Includes BOTH phases:
    the answer-provider calls AND the extraction pass (one extraction call per stored
    answer). Counts BASE runs only — the adaptive third run is conditional and cannot be
    projected, so actual cost may run slightly higher (documented tolerance)."""
    prompts = active_prompts(db, prompt_set.id)
    providers = provider_registry.enabled_providers()
    runs_per = max(1, settings.answer_tracking_runs_per_prompt)
    search = settings.answer_tracking_enable_search
    calls_per_provider = len(prompts) * runs_per
    # Answer calls use the SEARCH rate when web search is enabled (FIX: search is billed);
    # extraction (below) always uses the base rate.
    per_provider = [
        {"provider": p.name, "model": p.model, "calls": calls_per_provider,
         "cost_usd": round(calls_per_provider * provider_registry.call_rate(p.name, search), 6)}
        for p in providers
    ]
    answer_calls = calls_per_provider * len(providers)
    answer_cost = round(sum(pp["cost_usd"] for pp in per_provider), 6)

    # Extraction: ONE call per stored answer (FIX3 — this was previously ignored, making
    # the estimate ~half the real cost). Priced at the extraction provider's rate.
    ex_provider = settings.answer_tracking_extraction_provider.strip().lower()
    extraction_calls = answer_calls
    extraction_cost = round(extraction_calls * provider_registry.rate_for(ex_provider), 6)
    total_cost = round(answer_cost + extraction_cost, 6)

    return {
        "prompt_set_id": prompt_set.id,
        "active_prompts": len(prompts),
        "runs_per_prompt": runs_per,
        "providers": [p.name for p in providers],
        "call_count": answer_calls,                 # answer-provider calls
        "search_enabled": search,                   # answer calls priced with search when true
        "answer_cost_usd": answer_cost,
        "extraction_calls": extraction_calls,
        "extraction_provider": ex_provider,
        "extraction_cost_usd": extraction_cost,
        "estimated_cost_usd": total_cost,           # answer + extraction (the real total)
        "per_provider": per_provider,
        "note": "Base runs only; the adaptive third run is conditional and not included, "
                "so actual cost may be a little higher.",
    }


# --------------------------- run guards ---------------------------
def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def runs_this_month(db: Session, org_id: str, now: datetime) -> int:
    return (db.query(PromptRun)
            .filter(PromptRun.organization_id == org_id,
                    PromptRun.created_at >= _month_start(now))
            .count())


def last_consuming_run(db: Session, prompt_set_id: str) -> PromptRun | None:
    return (db.query(PromptRun)
            .filter(PromptRun.prompt_set_id == prompt_set_id,
                    PromptRun.status.in_(_CONSUMING_STATUSES))
            .order_by(PromptRun.created_at.desc())
            .first())


def create_run(db: Session, prompt_set: PromptSet, *, now: datetime | None = None,
               can_override: bool = False, override: bool = False,
               actor_user_id: str | None = None) -> PromptRun:
    """Create a PENDING run after enforcing the cost guards. Raises RunRefused when a
    guard blocks it. `override` (honored only when `can_override`) bypasses the dedup
    INTERVAL — never the monthly limit — and every applied override is logged."""
    now = now or datetime.utcnow()

    # 1) Dedup interval — refuse if a run of this set completed too recently.
    interval = timedelta(hours=settings.answer_tracking_min_run_interval_hours)
    last = last_consuming_run(db, prompt_set.id)
    if last is not None:
        anchor = last.completed_at or last.started_at or last.created_at
        next_eligible = anchor + interval
        if now < next_eligible:
            if override and can_override:
                log.warning(
                    "answer-tracking: interval override applied org=%s prompt_set=%s "
                    "user=%s next_eligible=%s",
                    prompt_set.organization_id, prompt_set.id, actor_user_id,
                    next_eligible.isoformat(),
                )
            else:
                raise RunRefused(
                    f"A run for this prompt set completed recently. Next run allowed at "
                    f"{next_eligible.isoformat()}Z.",
                    reason="interval", next_eligible=next_eligible,
                )

    # 2) Monthly run limit — always enforced (config-only, NOT a plan tier).
    limit = settings.answer_tracking_monthly_run_limit
    if limit is not None and runs_this_month(db, prompt_set.organization_id, now) >= limit:
        raise RunRefused(
            f"Monthly run limit reached ({limit} runs this month for this organization).",
            reason="monthly_limit",
        )

    run = PromptRun(organization_id=prompt_set.organization_id, prompt_set_id=prompt_set.id)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# --------------------------- scheduling due-logic ---------------------------
def is_due(db: Session, prompt_set: PromptSet, now: datetime) -> bool:
    """True when this set is due for a scheduled run: it has had at least one prior run
    and at least ANSWER_TRACKING_FREQUENCY_DAYS have elapsed since that run started.
    biweekly => 14 days apart (NOT twice per week). The very first run is manual, so a
    set with no prior run is never auto-scheduled."""
    last = last_consuming_run(db, prompt_set.id)
    if last is None or last.started_at is None:
        return False
    elapsed = now - last.started_at
    return elapsed >= timedelta(days=settings.answer_tracking_frequency_days)


def due_prompt_sets(db: Session, now: datetime) -> list[PromptSet]:
    return [ps for ps in db.query(PromptSet).all() if is_due(db, ps, now)]
