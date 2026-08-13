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
    Monitor, PromptResult, PromptResultAnalysis, PromptRun, PromptSet, TrackedPrompt,
)
from app.db.session import get_db
from app.schemas.answer_tracking import (
    CreatePromptRequest, CreatePromptSetRequest, UpdatePromptRequest, UpdatePromptSetRequest,
)
from app.services.answer_tracking import aggregation, extraction, runner, service
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
    citations, competitor share, sentiment, and average position."""
    run = _owned_run(db, ctx, run_id)
    return aggregation.run_summary(db, run)


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
            "is_adaptive_run": r.is_adaptive_run, "raw_response": r.raw_response, "error": r.error,
            "brand_mentioned": (a.brand_mentioned if a else None),
            "mention_context": (a.mention_context if a else None),
            "sentiment": (a.sentiment if a else None),
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
    return {"run_id": run.id, "extraction_status": run.extraction_status,
            "analyzed": result.get("analyzed", 0)}
