"""Execution engine for answer tracking.

`execute_run` runs every active prompt x enabled provider x run_index, with bounded
concurrency and a single retry on transient failures. EVERY call is persisted —
including failures (a failed call is data, not a gap). One provider failing never
aborts the run: the run is marked `partial` and the other results are kept.

This module contains the ONLY mention logic in Part A: `_provisional_mention_check`,
a deliberately narrow, temporary check used solely to decide the adaptive third run.
Part B replaces it with the real detector.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    RUN_COMPLETED, RUN_FAILED, RUN_PARTIAL, RUN_RUNNING,
    Monitor, PromptResult, PromptRun, PromptSet, TrackedPrompt,
)

from . import providers as provider_registry
from .providers import ProviderError, is_transient

log = logging.getLogger("app.answer_tracking.runner")

_RETRY_BACKOFF_SECONDS = 0.2


# --------------------------- provisional mention check ---------------------------
def _domain_root(url: str | None) -> str:
    """Second-level label of a host, lowercased ('example' from example.com or
    https://www.example.co.uk/x). Best-effort; empty when it can't be derived."""
    if not url:
        return ""
    host = url.strip().lower()
    if "//" not in host:
        host = "//" + host          # let urlparse find the netloc for a bare host
    host = urlparse(host).netloc or ""
    host = host.split("@")[-1].split(":")[0]        # drop creds/port
    if host.startswith("www."):
        host = host[4:]
    labels = [p for p in host.split(".") if p]
    if len(labels) >= 2:
        return labels[-2]
    return labels[0] if labels else ""


def _provisional_mention_check(monitor: Monitor | None, text: str | None) -> bool:
    """TEMPORARY (Part A only). Case-insensitive presence of the monitor's domain root
    OR brand name in the response text. This is intentionally naive — NO alias handling,
    NO fuzzy matching, NO entity linking. Part B REPLACES this with the real mention/
    citation detector. Do not expand it here.

    Used ONLY to decide whether the base runs disagree (and thus whether to fire the
    adaptive third run). Never persisted as a result field in Part A."""
    if not monitor or not text:
        return False
    hay = text.lower()
    root = _domain_root(monitor.normalized_url or monitor.url)
    brand = (monitor.name or "").strip().lower()
    signals = []
    if root:
        signals.append(root in hay)
    if brand:
        signals.append(brand in hay)
    return any(signals)


# --------------------------- one call ---------------------------
async def _one_call(provider, prompt: TrackedPrompt, *, run_index: int,
                    is_adaptive: bool, search: bool, sem: asyncio.Semaphore) -> dict:
    """Execute a single provider call (with one transient retry) and return a record
    dict. Never raises — a failure is captured in the record's `error`."""
    timeout = settings.answer_tracking_query_timeout_seconds
    rec = {
        "prompt_obj": prompt, "provider_obj": provider,
        "prompt_id": prompt.id, "provider": provider.name, "model": provider.model,
        "run_index": run_index, "is_adaptive_run": is_adaptive, "search_enabled": search,
        "raw_response": None, "citations": None, "latency_ms": None,
        "token_usage": None, "error": None, "ok": False, "text": "",
    }
    async with sem:
        for attempt in range(2):        # initial try + at most one retry
            try:
                res = await provider.query(prompt.text, timeout=timeout)
                rec.update(raw_response=res.text, text=res.text or "", citations=res.citations,
                           latency_ms=res.latency_ms, token_usage=res.tokens,
                           model=res.model or provider.model, ok=True, error=None)
                return rec
            except ProviderError as exc:
                # Retry ONCE on transient failures (timeout / connection / 429 / 5xx),
                # NEVER on 4xx auth/validation.
                if attempt == 0 and is_transient(exc):
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                rec["error"] = f"{provider.name}:{exc}"[:500]
                return rec
            except Exception as exc:    # noqa: BLE001 — unexpected error is still just a failed call
                rec["error"] = f"{provider.name}:{type(exc).__name__}"[:500]
                return rec
    return rec


async def _run_calls(specs: list[tuple], *, sem: asyncio.Semaphore, search: bool) -> list[dict]:
    """specs: (provider, prompt, run_index, is_adaptive). Runs them concurrently."""
    tasks = [
        _one_call(provider, prompt, run_index=idx, is_adaptive=adaptive, search=search, sem=sem)
        for (provider, prompt, idx, adaptive) in specs
    ]
    return list(await asyncio.gather(*tasks)) if tasks else []


# --------------------------- orchestration ---------------------------
async def execute_run(db: Session, run: PromptRun) -> PromptRun:
    """Execute a prepared run to completion. Persists every result (successes AND
    failures), accumulates estimated cost, and sets the final status. Never raises for
    an individual call failure; the run is `partial` when some calls fail."""
    run.status = RUN_RUNNING
    run.started_at = run.started_at or datetime.utcnow()
    db.commit()

    ps = db.get(PromptSet, run.prompt_set_id)
    monitor = db.get(Monitor, ps.monitor_id) if ps and ps.monitor_id else None
    prompts = (db.query(TrackedPrompt)
               .filter(TrackedPrompt.prompt_set_id == run.prompt_set_id,
                       TrackedPrompt.is_active.is_(True))
               .order_by(TrackedPrompt.created_at)
               .all())[:settings.answer_tracking_max_prompts]
    providers = provider_registry.enabled_providers()
    search = settings.answer_tracking_enable_search
    runs_per = max(1, settings.answer_tracking_runs_per_prompt)
    sem = asyncio.Semaphore(max(1, settings.answer_tracking_max_concurrency))

    # ---- base runs: prompt x provider x run_index ----
    base_specs = [
        (provider, prompt, idx, False)
        for prompt in prompts for provider in providers for idx in range(runs_per)
    ]
    base_records = await _run_calls(base_specs, sem=sem, search=search)

    # ---- adaptive third run: only when base runs DISAGREE on mention ----
    adaptive_records: list[dict] = []
    if settings.answer_tracking_adaptive_third_run and monitor and base_records:
        groups: dict[tuple, list[dict]] = {}
        for rec in base_records:
            groups.setdefault((rec["prompt_id"], rec["provider"]), []).append(rec)
        adaptive_specs = []
        for (_prompt_id, _provider), recs in groups.items():
            mentions = {
                _provisional_mention_check(monitor, r["text"]) for r in recs if r["ok"]
            }
            if len(mentions) > 1:       # some base runs said mentioned, others did not
                provider = recs[0]["provider_obj"]
                prompt = recs[0]["prompt_obj"]
                adaptive_specs.append((provider, prompt, runs_per, True))
        adaptive_records = await _run_calls(adaptive_specs, sem=sem, search=search)

    all_records = base_records + adaptive_records

    # ---- persist every result (success + failure) ----
    total_cost = 0.0
    for rec in all_records:
        db.add(PromptResult(
            run_id=run.id, prompt_id=rec["prompt_id"], organization_id=run.organization_id,
            provider=rec["provider"], model=rec["model"], run_index=rec["run_index"],
            is_adaptive_run=rec["is_adaptive_run"], search_enabled=rec["search_enabled"],
            raw_response=rec["raw_response"], citations=rec["citations"],
            latency_ms=rec["latency_ms"], token_usage=rec["token_usage"], error=rec["error"],
        ))
        total_cost += provider_registry.rate_for(rec["provider"])

    total = len(all_records)
    failed = sum(1 for r in all_records if r["error"])
    run.total_calls = total
    run.failed_calls = failed
    run.estimated_cost_usd = round(total_cost, 6)
    if total == 0 or failed == 0:
        run.status = RUN_COMPLETED
    elif failed == total:
        run.status = RUN_FAILED
    else:
        run.status = RUN_PARTIAL
    run.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run
