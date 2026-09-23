"""AI Answer Tracking endpoints — prompt-set/prompt CRUD + brand identity, cost estimate,
run trigger, run status, and (Part B) Share-of-Voice summary, run-over-run trend, and
admin reanalyse. All authenticated + org-scoped (explicit cross-org 404).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_permission
from app.config import settings
from app.db.models import (
    EXTRACTION_PENDING, ROLE_ADMIN, ROLE_OWNER,
    Monitor, PromptResult, PromptResultAnalysis, PromptRun, PromptSet, Scan, TrackedPrompt,
)
from app.db.session import get_db
from app.schemas.answer_tracking import (
    CreatePromptRequest, CreatePromptSetRequest, UpdateMonitorBrandRequest,
    UpdatePromptRequest, UpdatePromptSetRequest,
)
from app.services.answer_tracking import (
    aggregation, extraction, gap_analysis, runner, service, visibility,
)
from app.services.answer_tracking.errors import RunRefused

router = APIRouter(tags=["answer-tracking"])


# ------------------------------- helpers -------------------------------
def _owned_set(db: Session, ctx: AuthContext, prompt_set_id: str) -> PromptSet:
    ps = service.owned_prompt_set(db, ctx.org_id, prompt_set_id)
    if ps is None:
        raise HTTPException(status_code=404, detail="Prompt set not found.")
    return ps


def _owned_prompt(db: Session, ctx: AuthContext, prompt_id: str) -> TrackedPrompt:
    p = db.get(TrackedPrompt, prompt_id)
    if not p or p.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Prompt not found.")
    return p


def _validate_monitor(db: Session, ctx: AuthContext, monitor_id: str | None) -> None:
    if monitor_id is None:
        return
    m = db.get(Monitor, monitor_id)
    if not m or m.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Monitor not found.")


def _set_out(db: Session, ps: PromptSet) -> dict:
    total = service.prompt_count(db, ps.id)
    active = (db.query(TrackedPrompt)
              .filter(TrackedPrompt.prompt_set_id == ps.id,
                      TrackedPrompt.is_active.is_(True)).count())
    return {
        "id": ps.id, "name": ps.name, "monitor_id": ps.monitor_id,
        "brand_name": ps.brand_name, "brand_domain": ps.brand_domain,
        "brand_aliases": ps.brand_aliases or [],
        "competitor_domains": ps.competitor_domains or [],
        "prompt_count": total, "active_prompt_count": active,
        "created_at": ps.created_at, "updated_at": ps.updated_at,
        "max_prompts": settings.answer_tracking_max_prompts,
    }


def _prompt_out(p: TrackedPrompt) -> dict:
    return {"id": p.id, "prompt_set_id": p.prompt_set_id, "text": p.text,
            "is_active": p.is_active, "created_at": p.created_at}


def _run_out(run: PromptRun) -> dict:
    """Run status + progress. `extraction_status` drives the UI phase (Running ->
    Analysing -> Complete); the analysis itself is served by the summary endpoint."""
    return {
        "id": run.id, "prompt_set_id": run.prompt_set_id, "status": run.status,
        "extraction_status": run.extraction_status,
        "total_calls": run.total_calls, "failed_calls": run.failed_calls,
        "estimated_cost_usd": run.estimated_cost_usd,
        "started_at": run.started_at, "completed_at": run.completed_at,
        "created_at": run.created_at,
    }


# ------------------------------- monitor-scoped answer tracking -------------------------------
# Prompts belong to a SITE (monitor). Each monitor has exactly one prompt list, backed
# internally by a single hidden PromptSet — the set concept is gone from the user's model.
def _owned_monitor(db: Session, ctx: AuthContext, monitor_id: str) -> Monitor:
    m = service.owned_monitor(db, ctx.org_id, monitor_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Monitor not found.")
    return m


def _monitor_brand_out(m: Monitor) -> dict:
    return {
        "monitor_id": m.id, "site_name": m.name, "site_url": m.normalized_url,
        "brand_name": m.brand_name or m.name, "brand_domain": m.brand_domain or m.normalized_url,
        "brand_aliases": m.brand_aliases or [], "competitor_domains": m.competitor_domains or [],
    }


@router.get("/monitors/{monitor_id}/answer-tracking")
def monitor_answer_tracking(monitor_id: str,
                            ctx: AuthContext = Depends(require_permission("report:view")),
                            db: Session = Depends(get_db)):
    """Everything the Answer Tracking page needs for ONE site: brand identity, prompts,
    recent runs, the pre-run estimate, the trend, and starter-prompt suggestions."""
    m = _owned_monitor(db, ctx, monitor_id)
    ps = service.prompt_set_for_monitor(db, ctx.org_id, monitor_id, create=True)
    prompts = (db.query(TrackedPrompt)
               .filter(TrackedPrompt.prompt_set_id == ps.id)
               .order_by(TrackedPrompt.created_at).all())
    runs = (db.query(PromptRun)
            .filter(PromptRun.prompt_set_id == ps.id)
            .order_by(PromptRun.created_at.desc()).limit(20).all())
    return {
        **_monitor_brand_out(m),
        "max_prompts": settings.answer_tracking_max_prompts,
        "prompts": [_prompt_out(p) for p in prompts],
        "runs": [_run_out(r) for r in runs],
        "suggestions": service.suggest_prompts(m),
        "estimate": service.estimate_run(db, ps),
        "trend": aggregation.set_trend(db, ps, n=10),
    }


@router.patch("/monitors/{monitor_id}/answer-tracking")
def update_monitor_brand(monitor_id: str, body: UpdateMonitorBrandRequest,
                         ctx: AuthContext = Depends(require_permission("scan:run")),
                         db: Session = Depends(get_db)):
    m = _owned_monitor(db, ctx, monitor_id)
    if body.brand_name is not None:
        m.brand_name = body.brand_name.strip() or None
    if body.brand_domain is not None:
        m.brand_domain = body.brand_domain.strip().lower() or None
    if body.brand_aliases is not None:
        m.brand_aliases = body.brand_aliases or None
    if body.competitor_domains is not None:
        m.competitor_domains = body.competitor_domains or None
    db.commit()
    db.refresh(m)
    return _monitor_brand_out(m)


@router.post("/monitors/{monitor_id}/answer-tracking/prompts")
def monitor_add_prompt(monitor_id: str, body: CreatePromptRequest,
                       ctx: AuthContext = Depends(require_permission("scan:run")),
                       db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    ps = service.prompt_set_for_monitor(db, ctx.org_id, monitor_id, create=True)
    cap = settings.answer_tracking_max_prompts
    if service.prompt_count(db, ps.id) >= cap:
        raise HTTPException(status_code=422,
                            detail=f"This site already has the maximum of {cap} prompts.")
    p = TrackedPrompt(prompt_set_id=ps.id, organization_id=ctx.org_id, text=body.text.strip())
    db.add(p)
    db.commit()
    db.refresh(p)
    return _prompt_out(p)


@router.get("/monitors/{monitor_id}/answer-tracking/estimate")
def monitor_estimate(monitor_id: str,
                     ctx: AuthContext = Depends(require_permission("report:view")),
                     db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    ps = service.prompt_set_for_monitor(db, ctx.org_id, monitor_id, create=True)
    return service.estimate_run(db, ps)


@router.get("/monitors/{monitor_id}/answer-tracking/trend")
def monitor_trend(monitor_id: str, n: int = Query(default=10, ge=2, le=50),
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    ps = service.prompt_set_for_monitor(db, ctx.org_id, monitor_id, create=True)
    return aggregation.set_trend(db, ps, n=n)


@router.post("/monitors/{monitor_id}/answer-tracking/run")
async def monitor_run(monitor_id: str,
                      override: bool = Query(default=False),
                      ctx: AuthContext = Depends(require_permission("scan:run")),
                      db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    ps = service.prompt_set_for_monitor(db, ctx.org_id, monitor_id, create=True)
    can_override = ctx.role in (ROLE_ADMIN, ROLE_OWNER)
    try:
        run = service.create_run(db, ps, now=datetime.utcnow(), can_override=can_override,
                                  override=override, actor_user_id=ctx.user.id)
    except RunRefused as exc:
        raise HTTPException(status_code=429, detail=exc.detail,
                            headers={"X-Run-Refused-Reason": exc.reason})
    await runner.execute_run(db, run)
    return {"run_id": run.id, "status": run.status}


# ------------------------------- prompt sets -------------------------------
@router.post("/prompt-sets")
def create_prompt_set(body: CreatePromptSetRequest,
                      ctx: AuthContext = Depends(require_permission("scan:run")),
                      db: Session = Depends(get_db)):
    _validate_monitor(db, ctx, body.monitor_id)
    # Seed brand_name/brand_domain from the linked monitor when the caller omits them,
    # but keep them independently editable (legal name != marketed brand name).
    monitor = db.get(Monitor, body.monitor_id) if body.monitor_id else None
    brand_name = body.brand_name if body.brand_name is not None else (monitor.name if monitor else None)
    brand_domain = (body.brand_domain if body.brand_domain is not None
                    else (monitor.normalized_url if monitor else None))
    ps = PromptSet(
        organization_id=ctx.org_id, name=body.name.strip(), monitor_id=body.monitor_id,
        brand_name=(brand_name or None), brand_domain=(brand_domain or None),
        brand_aliases=body.brand_aliases or None,
        competitor_domains=body.competitor_domains or None,
    )
    db.add(ps)
    db.commit()
    db.refresh(ps)
    return _set_out(db, ps)


@router.get("/prompt-sets")
def list_prompt_sets(ctx: AuthContext = Depends(require_permission("report:view")),
                     db: Session = Depends(get_db)):
    sets = (db.query(PromptSet)
            .filter(PromptSet.organization_id == ctx.org_id)
            .order_by(PromptSet.created_at.desc()).all())
    return {"prompt_sets": [_set_out(db, ps) for ps in sets]}


@router.get("/prompt-sets/{prompt_set_id}")
def get_prompt_set(prompt_set_id: str,
                   ctx: AuthContext = Depends(require_permission("report:view")),
                   db: Session = Depends(get_db)):
    ps = _owned_set(db, ctx, prompt_set_id)
    prompts = (db.query(TrackedPrompt)
               .filter(TrackedPrompt.prompt_set_id == ps.id)
               .order_by(TrackedPrompt.created_at).all())
    runs = (db.query(PromptRun)
            .filter(PromptRun.prompt_set_id == ps.id)
            .order_by(PromptRun.created_at.desc()).limit(20).all())
    return {**_set_out(db, ps),
            "prompts": [_prompt_out(p) for p in prompts],
            "runs": [_run_out(r) for r in runs]}


@router.patch("/prompt-sets/{prompt_set_id}")
def update_prompt_set(prompt_set_id: str, body: UpdatePromptSetRequest,
                      ctx: AuthContext = Depends(require_permission("scan:run")),
                      db: Session = Depends(get_db)):
    ps = _owned_set(db, ctx, prompt_set_id)
    if body.name is not None:
        ps.name = body.name.strip()
    if body.monitor_id is not None:
        _validate_monitor(db, ctx, body.monitor_id)
        ps.monitor_id = body.monitor_id or None
    if body.brand_name is not None:
        ps.brand_name = body.brand_name.strip() or None
    if body.brand_domain is not None:
        ps.brand_domain = body.brand_domain.strip().lower() or None
    if body.brand_aliases is not None:
        ps.brand_aliases = body.brand_aliases or None
    if body.competitor_domains is not None:
        ps.competitor_domains = body.competitor_domains or None
    db.commit()
    db.refresh(ps)
    return _set_out(db, ps)


@router.delete("/prompt-sets/{prompt_set_id}")
def delete_prompt_set(prompt_set_id: str,
                      ctx: AuthContext = Depends(require_permission("scan:delete")),
                      db: Session = Depends(get_db)):
    ps = _owned_set(db, ctx, prompt_set_id)
    # Cascade manually (no FK constraints): prompts, runs, results, and analyses.
    run_ids = [r[0] for r in db.query(PromptRun.id)
               .filter(PromptRun.prompt_set_id == ps.id).all()]
    if run_ids:
        db.query(PromptResultAnalysis).filter(
            PromptResultAnalysis.run_id.in_(run_ids)).delete(synchronize_session=False)
        db.query(PromptResult).filter(PromptResult.run_id.in_(run_ids)).delete(
            synchronize_session=False)
    db.query(PromptRun).filter(PromptRun.prompt_set_id == ps.id).delete(
        synchronize_session=False)
    db.query(TrackedPrompt).filter(TrackedPrompt.prompt_set_id == ps.id).delete(
        synchronize_session=False)
    db.delete(ps)
    db.commit()
    return {"ok": True, "id": prompt_set_id, "status": "deleted"}


# ------------------------------- prompts -------------------------------
@router.post("/prompt-sets/{prompt_set_id}/prompts")
def add_prompt(prompt_set_id: str, body: CreatePromptRequest,
               ctx: AuthContext = Depends(require_permission("scan:run")),
               db: Session = Depends(get_db)):
    ps = _owned_set(db, ctx, prompt_set_id)
    # Hard cap enforced at the service layer (counts EVERY prompt in the set).
    cap = settings.answer_tracking_max_prompts
    if service.prompt_count(db, ps.id) >= cap:
        raise HTTPException(status_code=422,
                            detail=f"This prompt set already has the maximum of {cap} prompts.")
    p = TrackedPrompt(prompt_set_id=ps.id, organization_id=ctx.org_id, text=body.text.strip())
    db.add(p)
    db.commit()
    db.refresh(p)
    return _prompt_out(p)


@router.get("/prompt-sets/{prompt_set_id}/prompts")
def list_prompts(prompt_set_id: str,
                 ctx: AuthContext = Depends(require_permission("report:view")),
                 db: Session = Depends(get_db)):
    ps = _owned_set(db, ctx, prompt_set_id)
    prompts = (db.query(TrackedPrompt)
               .filter(TrackedPrompt.prompt_set_id == ps.id)
               .order_by(TrackedPrompt.created_at).all())
    return {"prompts": [_prompt_out(p) for p in prompts]}


@router.patch("/prompts/{prompt_id}")
def update_prompt(prompt_id: str, body: UpdatePromptRequest,
                  ctx: AuthContext = Depends(require_permission("scan:run")),
                  db: Session = Depends(get_db)):
    p = _owned_prompt(db, ctx, prompt_id)
    if body.text is not None:
        p.text = body.text.strip()
    if body.is_active is not None:
        p.is_active = body.is_active
    db.commit()
    db.refresh(p)
    return _prompt_out(p)


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: str,
                  ctx: AuthContext = Depends(require_permission("scan:run")),
                  db: Session = Depends(get_db)):
    p = _owned_prompt(db, ctx, prompt_id)
    db.delete(p)
    db.commit()
    return {"ok": True, "id": prompt_id, "status": "deleted"}


# ------------------------------- estimate + run -------------------------------
@router.get("/prompt-sets/{prompt_set_id}/estimate")
def estimate(prompt_set_id: str,
             ctx: AuthContext = Depends(require_permission("report:view")),
             db: Session = Depends(get_db)):
    ps = _owned_set(db, ctx, prompt_set_id)
    return service.estimate_run(db, ps)


@router.post("/prompt-sets/{prompt_set_id}/run")
async def run_prompt_set(prompt_set_id: str,
                         override: bool = Query(default=False),
                         ctx: AuthContext = Depends(require_permission("scan:run")),
                         db: Session = Depends(get_db)):
    ps = _owned_set(db, ctx, prompt_set_id)
    can_override = ctx.role in (ROLE_ADMIN, ROLE_OWNER)     # only admin-role may override the interval
    try:
        run = service.create_run(db, ps, now=datetime.utcnow(), can_override=can_override,
                                  override=override, actor_user_id=ctx.user.id)
    except RunRefused as exc:
        # A curated, user-facing string (it names the next eligible time for the interval
        # case) so the frontend surfaces it directly. `reason` is echoed in a header for
        # programmatic callers without breaking the string-detail contract.
        raise HTTPException(status_code=429, detail=exc.detail,
                            headers={"X-Run-Refused-Reason": exc.reason})
    await runner.execute_run(db, run)
    return {"run_id": run.id, "status": run.status}


@router.get("/prompt-runs/{run_id}")
def get_run(run_id: str,
            ctx: AuthContext = Depends(require_permission("report:view")),
            db: Session = Depends(get_db)):
    run = db.get(PromptRun, run_id)
    if not run or run.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Run not found.")
    results = db.query(PromptResult).filter(PromptResult.run_id == run.id).count()
    return {**_run_out(run), "result_count": results}


# ------------------------------- analysis / results (Part B) -------------------------------
def _owned_run(db: Session, ctx: AuthContext, run_id: str) -> PromptRun:
    run = db.get(PromptRun, run_id)
    if not run or run.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.get("/prompt-runs/{run_id}/summary")
def run_summary(run_id: str,
                ctx: AuthContext = Depends(require_permission("report:view")),
                db: Session = Depends(get_db)):
    """Share-of-Voice summary for one run: mention rate (across ALL samples, excluding
    extraction failures from the denominator), per-provider + per-prompt breakdowns,
    citations, competitor share, sentiment, and average position.

    Gated (Decision 1): FREE keeps every basic/operational field — mention_rate and
    is_gap for EVERY prompt, sentiment, average position — since "did my brand appear"
    is never locked. The deeper competitor/gap/provider/citation intelligence (now
    consolidated in AI Visibility) is trimmed to a small preview + locked counts, via
    the same granular entitlement flags `gate_visibility` already uses — no second
    paywall mechanism."""
    run = _owned_run(db, ctx, run_id)
    from app.billing import entitlements
    access = entitlements.ai_visibility_access(db, ctx.org_id)
    return visibility.gate_run_summary(aggregation.run_summary(db, run), access)


def _scan_report_for_run(db: Session, run: PromptRun) -> dict | None:
    """The linked site's latest scan report (for cross-linking score-loss opportunities),
    or None. Best-effort — a missing/failed scan must never break AI Visibility."""
    if not run.monitor_id:
        return None
    m = db.get(Monitor, run.monitor_id)
    if not m or not getattr(m, "latest_scan_id", None):
        return None
    scan = db.get(Scan, m.latest_scan_id)
    if not scan:
        return None
    try:
        from app.reports.engine import build_report
        from app.reports.service import scan_to_input
        return build_report(scan_to_input(scan))
    except Exception:   # noqa: BLE001 — cross-link is optional
        return None


def _gate_visibility(payload: dict, access: dict) -> dict:
    """Backward-compatible shim → the shared, pure gating rule in the visibility service
    (kept so both the run and report endpoints trim identically)."""
    return visibility.gate_visibility(payload, access)


@router.get("/prompt-runs/{run_id}/visibility")
def run_visibility(run_id: str,
                   ctx: AuthContext = Depends(require_permission("report:view")),
                   db: Session = Depends(get_db)):
    """Phase 3 — the first-class AI Visibility layer for one run: negative-first coverage,
    answerability, content gaps (grounded), competitor intelligence, and the deterministic
    AEO Opportunity Finder (cross-linked to the site's scan where one exists). Org-scoped
    (a run outside your org is 404); gated per entitlement (Free preview vs Pro full)."""
    run = _owned_run(db, ctx, run_id)
    summary = aggregation.run_summary(db, run)
    ps = db.get(PromptSet, run.prompt_set_id)
    trend = aggregation.set_trend(db, ps, n=10) if ps else None
    scan_report = _scan_report_for_run(db, run)
    payload = visibility.build_ai_visibility(summary, scan_report=scan_report, trend=trend)
    from app.billing import entitlements
    return _gate_visibility(payload, entitlements.ai_visibility_access(db, ctx.org_id))


@router.get("/prompt-runs/{run_id}/results")
def run_results(run_id: str,
                ctx: AuthContext = Depends(require_permission("report:view")),
                db: Session = Depends(get_db)):
    """Raw provider responses for a run, grouped by prompt, each joined to its analysis
    (brand_mentioned + mention_context) so the UI can expand a prompt and highlight the
    mention. Org-scoped."""
    run = _owned_run(db, ctx, run_id)
    results = db.query(PromptResult).filter(PromptResult.run_id == run.id).all()
    analyses = {a.result_id: a for a in
                db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id).all()}
    texts = {p.id: p.text for p in db.query(TrackedPrompt)
             .filter(TrackedPrompt.prompt_set_id == run.prompt_set_id).all()}
    grouped: dict[str, dict] = {}
    for r in results:
        g = grouped.setdefault(r.prompt_id, {"prompt_id": r.prompt_id,
                                             "text": texts.get(r.prompt_id, ""), "results": []})
        a = analyses.get(r.id)
        g["results"].append({
            "provider": r.provider, "model": r.model, "run_index": r.run_index,
            "is_adaptive_run": r.is_adaptive_run, "search_enabled": r.search_enabled,
            "citations": r.citations, "raw_response": r.raw_response, "error": r.error,
            # structured verdict (Task 1) — the UI renders this and keeps raw_response collapsed
            "brand_mentioned": (a.brand_mentioned if a else None),
            "mention_context": (a.mention_context if a else None),
            "sentiment": (a.sentiment if a else None),
            "brand_urls_cited": (a.brand_urls_cited if a else None),
            # Provenance for brand_urls_cited (Phase H): the provider itself returned
            # `citations` (a real list, possibly empty) -> those URLs are provider-
            # reported ("provider"). The provider CAN'T report citations (`citations`
            # is None) -> _normalize() falls back to our own LLM extracting URLs from
            # the answer text instead ("llm_extracted"). Mirrors extraction.py's own
            # `result.citations is None` branch exactly — never a second classifier.
            "citation_source": "provider" if r.citations is not None else "llm_extracted",
            "position": (a.position if a else None),
            "recommended_entities": (a.recommended_entities if a else None),
            "extraction_failed": (a.extraction_failed if a else None),
        })
    return {"run_id": run.id, "prompts": list(grouped.values())}


@router.get("/prompt-sets/{prompt_set_id}/trend")
def set_trend(prompt_set_id: str,
              n: int = Query(default=10, ge=2, le=50),
              ctx: AuthContext = Depends(require_permission("report:view")),
              db: Session = Depends(get_db)):
    """Mention rate, citation count, and competitor share across the last N runs, with a
    marker on any run where the extraction or answer model strings changed."""
    ps = _owned_set(db, ctx, prompt_set_id)
    return aggregation.set_trend(db, ps, n=n)


@router.post("/prompt-runs/{run_id}/reanalyse")
async def reanalyse_run(run_id: str,
                        ctx: AuthContext = Depends(require_permission("scan:run")),
                        db: Session = Depends(get_db)):
    """Re-run extraction against the STORED raw responses — never re-calls the answer
    providers. Admin-role only (owner/admin) since it incurs extraction cost."""
    run = _owned_run(db, ctx, run_id)
    if ctx.role not in (ROLE_OWNER, ROLE_ADMIN):
        raise HTTPException(status_code=403,
                            detail="Only an owner or admin can re-analyse a run.")
    run.extraction_status = EXTRACTION_PENDING
    db.commit()
    result = await extraction.extract_for_run(db, run)
    gap = await gap_analysis.run_gap_analysis_for_run(db, run)   # regenerate gap-to-action too
    return {"run_id": run.id, "extraction_status": run.extraction_status,
            "analyzed": result.get("analyzed", 0), "gap_analyses": gap.get("generated", 0)}
