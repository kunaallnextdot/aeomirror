"""Part B — gap-to-action.

For every prompt where the brand has a 0% mention rate in a run, make ONE cheap LLM call
(never one per sample) that answers: why did the brand likely not surface for this query,
and what 2-4 concrete things can the user do about it. Every recommendation must be
grounded in data ALREADY held — the site's own scan findings (crawler access, failing
sections) and what the recommended entities have that the brand does not. If there is not
enough signal, the model must say so plainly rather than invent generic SEO advice.

Reads stored analysis + the linked monitor's latest scan only — it NEVER calls the answer
providers. Cached per prompt per run in prompt_gap_analysis (regenerated on reanalyse).
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    Monitor, PromptGapAnalysis, PromptResult, PromptResultAnalysis, PromptRun, PromptSet,
    Scan, TrackedPrompt,
)

from . import providers as provider_registry
from .extraction import brand_context

log = logging.getLogger("app.answer_tracking.gap_analysis")

_SOLUTION_TYPES = ("recommendation", "comparison", None)   # None = untyped (legacy) counts as solution


# --------------------------- brand findings (already-held data) ---------------------------
def _brand_findings(db: Session, ps: PromptSet) -> str:
    """A compact summary of the brand's OWN latest scan (crawler access + weak sections +
    score) so the model grounds its advice in real data. Empty string when none."""
    if not ps or not ps.monitor_id:
        return ""
    monitor = db.get(Monitor, ps.monitor_id)
    if not monitor or not monitor.latest_scan_id:
        return ""
    scan = db.get(Scan, monitor.latest_scan_id)
    result = (scan.result if scan else None) or {}
    parts = []
    score = result.get("overall_score")
    if score is not None:
        parts.append(f"Overall AI-visibility score: {score}/100.")
    blocked = [f.get("bot_name") for f in (result.get("crawler_access", {}) or {}).get("findings", [])
               if isinstance(f, dict) and f.get("status", "").startswith("blocked")]
    if blocked:
        parts.append("AI crawlers BLOCKED from the site: " + ", ".join([b for b in blocked if b]) + ".")
    weak = [f"{s.get('label') or s.get('id')} ({s.get('status')})"
            for s in (result.get("sections") or [])
            if isinstance(s, dict) and s.get("status") in ("fail", "warn")]
    if weak:
        parts.append("Failing/weak on-site signals: " + "; ".join(weak[:8]) + ".")
    return " ".join(parts)


# --------------------------- prompt + parse ---------------------------
def build_gap_prompt(prompt_text: str, recommended: list[str], brand: dict,
                     findings: str) -> str:
    rec = ", ".join(recommended) if recommended else "(the answer recommended no clear solutions)"
    return (
        "A brand is NOT being surfaced by an AI assistant for a query in its category. Using "
        "ONLY the data below, explain why and give concrete, grounded actions. Do NOT invent "
        "generic SEO advice: every action must tie to the scan findings, the crawler access "
        "status, or something the recommended entities appear to have that the brand does not. "
        "If the data is too thin to say anything specific, set has_signal=false and say so.\n\n"
        f"BRAND: {brand.get('name') or '(unknown)'}  DOMAIN: {brand.get('domain') or '(unknown)'}\n"
        f"QUERY THE BRAND MISSED: {prompt_text}\n"
        f"ENTITIES THE AI RECOMMENDED INSTEAD (in order): {rec}\n"
        f"THE BRAND'S OWN SCAN FINDINGS: {findings or '(no scan findings available)'}\n\n"
        "Return a single JSON object with EXACTLY these keys and nothing else:\n"
        '{"has_signal": true|false, '
        '"why": "at most 2 sentences on why the brand likely did not surface", '
        '"actions": ["2 to 4 concrete actions, each tied to the data above"]}\n'
        "Keep it tight — no essays. If has_signal is false, why should say plainly that there "
        "is not enough signal and actions should be an empty list."
    )


def parse_gap(text: str | None) -> dict | None:
    if not text or not text.strip():
        return None
    s = text.strip()
    first, last = s.find("{"), s.rfind("}")
    for cand in ([s[first:last + 1]] if first != -1 and last > first else []) + [s]:
        try:
            obj = json.loads(cand)
        except Exception:   # noqa: BLE001
            continue
        if isinstance(obj, dict) and ("why" in obj or "actions" in obj):
            return obj
    return None


def _normalize_gap(parsed: dict) -> dict:
    actions = [a.strip() for a in (parsed.get("actions") or [])
               if isinstance(a, str) and a.strip()][:4]
    has_signal = bool(parsed.get("has_signal", True)) and bool(actions or parsed.get("why"))
    return {"why": (parsed.get("why") or None), "actions": actions, "has_signal": has_signal}


# --------------------------- orchestration ---------------------------
def _zero_mention_prompts(db: Session, run: PromptRun) -> dict[str, list]:
    """Map prompt_id -> ordered recommended-entity names, for prompts with >=1 successful
    extraction but ZERO brand mentions."""
    results = {r.id: r for r in db.query(PromptResult).filter(PromptResult.run_id == run.id).all()}
    analyses = db.query(PromptResultAnalysis).filter(
        PromptResultAnalysis.run_id == run.id, PromptResultAnalysis.extraction_failed.is_(False)).all()
    total: dict[str, int] = {}
    hits: dict[str, int] = {}
    rec_order: dict[str, list] = {}
    for a in analyses:
        r = results.get(a.result_id)
        pid = r.prompt_id if r else None
        if not pid:
            continue
        total[pid] = total.get(pid, 0) + 1
        if a.brand_mentioned:
            hits[pid] = hits.get(pid, 0) + 1
        for e in (a.recommended_entities or []):
            nm = (e.get("name") if isinstance(e, dict) else str(e)).strip()
            if nm and nm not in rec_order.setdefault(pid, []):
                rec_order[pid].append(nm)
    return {pid: rec_order.get(pid, []) for pid in total
            if total[pid] > 0 and hits.get(pid, 0) == 0}


async def run_gap_analysis_for_run(db: Session, run: PromptRun) -> dict:
    """Generate/refresh gap-to-action for every zero-mention prompt in the run. Replaces
    prior rows (reanalyse-safe). Adds cost to the run. Never calls answer providers."""
    # Always clear prior rows so a reanalyse (or a flag flip to off) never leaves stale data.
    db.query(PromptGapAnalysis).filter(PromptGapAnalysis.run_id == run.id).delete(
        synchronize_session=False)
    db.commit()

    if not settings.answer_tracking_gap_analysis_enabled:
        return {"run_id": run.id, "generated": 0, "calls": 0, "skipped": "disabled"}

    provider = provider_registry.gap_analysis_provider()
    if provider is None:
        return {"run_id": run.id, "generated": 0, "calls": 0, "skipped": "no_provider"}

    ps = db.get(PromptSet, run.prompt_set_id)
    brand = brand_context(db, ps) if ps else {"name": "", "domain": ""}
    findings = _brand_findings(db, ps) if ps else ""
    texts = {p.id: p.text for p in db.query(TrackedPrompt)
             .filter(TrackedPrompt.prompt_set_id == run.prompt_set_id).all()}

    zero = _zero_mention_prompts(db, run)          # prompt_id -> recommended names
    timeout = settings.answer_tracking_query_timeout_seconds
    calls = 0
    generated = 0
    for pid, recommended in zero.items():
        prompt = build_gap_prompt(texts.get(pid, ""), recommended, brand, findings)
        rec = {"why": None, "actions": [], "has_signal": False}
        try:
            res = await provider.query(prompt, timeout=timeout)
            calls += 1
            parsed = parse_gap(res.text)
            if parsed is not None:
                rec = _normalize_gap(parsed)
        except Exception:   # noqa: BLE001 — a failure is not fatal; store an empty gap row
            log.exception("answer-tracking: gap analysis failed run=%s prompt=%s", run.id, pid)
        db.add(PromptGapAnalysis(
            run_id=run.id, prompt_id=pid, organization_id=run.organization_id,
            monitor_id=run.monitor_id,
            why=rec["why"], actions=rec["actions"], has_signal=rec["has_signal"],
            model=getattr(provider, "model", settings.answer_tracking_gap_analysis_model_resolved),
        ))
        generated += 1

    rate = provider_registry.rate_for(settings.answer_tracking_extraction_provider.strip().lower())
    run.estimated_cost_usd = round((run.estimated_cost_usd or 0.0) + calls * rate, 6)
    db.commit()
    return {"run_id": run.id, "generated": generated, "calls": calls}
