"""AEO Answer Simulator endpoints — the deterministic, zero-external-call-by-default
replacement for the core Answer Tracking flow. Kept in its own router rather than
folded into routes_answer_tracking.py: the simulator's request/response shapes
(evidence, answerability, missing_information) differ enough from provider-
tracking's that sharing handlers would force awkward branching. Reuses the SAME
underlying tables (prompt_runs/prompt_results/prompt_result_analysis/
tracked_prompts) and the same ownership/auth helpers as provider tracking — nothing
here duplicates runner.py's live-provider call/retry/persistence machinery, and
nothing here ever calls an external AI-provider adapter directly.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_permission
from app.billing import entitlements
from app.db.models import (
    PromptResult, PromptResultAnalysis, PromptRun, Scan, SimulatorEvidence, TrackedPrompt,
)
from app.db.session import get_db
from app.reports.phase4 import normalize_question_key
from app.reports.service import get_or_build_report
from app.schemas.answer_tracking import RunSimulationRequest
from app.services.answer_simulator import batch
from app.services.answer_simulator import service as sim_service
from app.services.answer_tracking import service, visibility

router = APIRouter(tags=["answer-simulator"])


def _owned_monitor(db: Session, ctx: AuthContext, monitor_id: str):
    m = service.owned_monitor(db, ctx.org_id, monitor_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Monitor not found.")
    return m


def _owned_run(db: Session, ctx: AuthContext, monitor_id: str, run_id: str) -> PromptRun:
    run = db.get(PromptRun, run_id)
    if (not run or run.organization_id != ctx.org_id or run.monitor_id != monitor_id
            or run.run_mode != "simulator"):
        raise HTTPException(status_code=404, detail="Simulation run not found.")
    return run


def _prompt_out(p: TrackedPrompt) -> dict:
    return {"id": p.id, "text": p.text, "source": p.source, "is_active": p.is_active}


@router.get("/monitors/{monitor_id}/answer-simulator/questions")
def simulator_questions(monitor_id: str,
                        ctx: AuthContext = Depends(require_permission("report:view")),
                        db: Session = Depends(get_db)):
    """Question Bank (scan-derived FAQ/heading questions) merged with this monitor's
    already-tracked prompts, each flagged with whether it's already tracked. No new
    question generation — reuses visibility.build_question_bank() as-is."""
    monitor = _owned_monitor(db, ctx, monitor_id)
    ps = service.prompt_set_for_monitor(db, ctx.org_id, monitor_id, create=True)
    tracked = (db.query(TrackedPrompt)
              .filter(TrackedPrompt.prompt_set_id == ps.id, TrackedPrompt.is_active.is_(True))
              .order_by(TrackedPrompt.created_at).all())
    tracked_keys = {normalize_question_key(p.text) for p in tracked}

    bank_questions: list[dict] = []
    if monitor.latest_scan_id:
        scan = db.get(Scan, monitor.latest_scan_id)
        if scan and scan.organization_id == ctx.org_id:
            scan_report, _row = get_or_build_report(db, scan)
            phase4 = scan_report.get("phase4") or {}
            if phase4.get("available") is not False:
                bank = visibility.build_question_bank(phase4.get("questions"), None, None)
                bank_questions = bank.get("questions", [])

    for q in bank_questions:
        q["already_tracked"] = normalize_question_key(q.get("question")) in tracked_keys

    return {
        "monitor_id": monitor.id,
        "bank_questions": bank_questions,
        "tracked_prompts": [_prompt_out(p) for p in tracked],
    }


def _find_or_create_prompt(db: Session, ps, text: str, source: str) -> TrackedPrompt:
    text = text.strip()
    key = normalize_question_key(text)
    existing = (db.query(TrackedPrompt)
               .filter(TrackedPrompt.prompt_set_id == ps.id, TrackedPrompt.is_active.is_(True))
               .all())
    for p in existing:
        if normalize_question_key(p.text) == key:
            return p
    p = TrackedPrompt(prompt_set_id=ps.id, organization_id=ps.organization_id, text=text, source=source)
    db.add(p)
    db.flush()
    return p


@router.post("/monitors/{monitor_id}/answer-simulator/run")
async def simulator_run(monitor_id: str, body: RunSimulationRequest,
                        ctx: AuthContext = Depends(require_permission("scan:run")),
                        db: Session = Depends(get_db)):
    monitor = _owned_monitor(db, ctx, monitor_id)
    ps = service.prompt_set_for_monitor(db, ctx.org_id, monitor_id, create=True)
    access = entitlements.answer_simulator_access(db, ctx.org_id)

    if body.request_llm_step and not access["llm_step"]:
        raise HTTPException(status_code=402,
                            detail="The optional AI explanation step is a Pro feature.")

    prompts: list[TrackedPrompt] = []
    seen_ids: set[str] = set()

    for pid in (body.question_ids or []):
        p = db.get(TrackedPrompt, pid)
        if not p or p.organization_id != ctx.org_id or p.prompt_set_id != ps.id:
            raise HTTPException(status_code=404, detail=f"Question {pid} not found.")
        if p.id not in seen_ids:
            prompts.append(p)
            seen_ids.add(p.id)

    for text in (body.custom_questions or []):
        if not text or not text.strip():
            continue
        p = _find_or_create_prompt(db, ps, text, "manual")
        if p.id not in seen_ids:
            prompts.append(p)
            seen_ids.add(p.id)

    for text in (body.question_bank_keys or []):
        if not text or not text.strip():
            continue
        p = _find_or_create_prompt(db, ps, text, "question_bank")
        if p.id not in seen_ids:
            prompts.append(p)
            seen_ids.add(p.id)

    if not prompts:
        raise HTTPException(status_code=422, detail="At least one question is required.")

    batch_limit = access["batch_limit"]
    locked_count = 0
    if batch_limit is not None and len(prompts) > batch_limit:
        locked_count = len(prompts) - batch_limit
        prompts = prompts[:batch_limit]

    run = sim_service.create_simulation_run(db, ps, llm_step_requested=body.request_llm_step)
    results = await batch.run_simulation_batch(db, run, monitor, prompts,
                                               request_llm_step=body.request_llm_step)
    results = [batch.gate_simulation_result(r, unlocked=access["full_evidence"]) for r in results]

    return {
        "run_id": run.id, "status": run.status, "results": results,
        "batch_limit": batch_limit, "locked_count": locked_count,
    }


@router.get("/monitors/{monitor_id}/answer-simulator/runs/{run_id}/results")
def simulator_results(monitor_id: str, run_id: str,
                      ctx: AuthContext = Depends(require_permission("report:view")),
                      db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    run = _owned_run(db, ctx, monitor_id, run_id)
    access = entitlements.answer_simulator_access(db, ctx.org_id)

    results = (db.query(PromptResult)
              .filter(PromptResult.run_id == run.id)
              .order_by(PromptResult.created_at).all())
    if not results:
        return {"run_id": run.id, "status": run.status, "results": []}

    result_ids = [r.id for r in results]
    prompt_ids = list({r.prompt_id for r in results})
    prompts_by_id = {p.id: p for p in
                     db.query(TrackedPrompt).filter(TrackedPrompt.id.in_(prompt_ids)).all()}
    analyses = {a.result_id: a for a in
               db.query(PromptResultAnalysis).filter(PromptResultAnalysis.result_id.in_(result_ids)).all()}
    evidence_by_result: dict[str, list] = {}
    for e in db.query(SimulatorEvidence).filter(SimulatorEvidence.result_id.in_(result_ids)).all():
        evidence_by_result.setdefault(e.result_id, []).append(e)

    out = [
        batch.gate_simulation_result(
            batch.format_simulation_result(
                r, analyses.get(r.id), evidence_by_result.get(r.id, []),
                prompts_by_id[r.prompt_id].text if r.prompt_id in prompts_by_id else "",
            ),
            unlocked=access["full_evidence"],
        )
        for r in results
    ]
    return {"run_id": run.id, "status": run.status, "results": out}


@router.post("/monitors/{monitor_id}/answer-simulator/runs/{run_id}/questions/{prompt_id}/explain")
async def simulator_explain(monitor_id: str, run_id: str, prompt_id: str,
                            ctx: AuthContext = Depends(require_permission("scan:run")),
                            db: Session = Depends(get_db)):
    """Forces the optional LLM step on ONE otherwise-skipped INSUFFICIENT_EVIDENCE
    question — the single carve-out from the "no LLM call for low-evidence questions"
    cost rule."""
    monitor = _owned_monitor(db, ctx, monitor_id)
    run = _owned_run(db, ctx, monitor_id, run_id)
    prompt = db.get(TrackedPrompt, prompt_id)
    if not prompt or prompt.organization_id != ctx.org_id or prompt.prompt_set_id != run.prompt_set_id:
        raise HTTPException(status_code=404, detail="Question not found.")

    access = entitlements.answer_simulator_access(db, ctx.org_id)
    if not access["llm_step"]:
        raise HTTPException(status_code=402,
                            detail="The optional AI explanation step is a Pro feature.")

    result = await batch.simulate_single_question(db, run, monitor, prompt)
    return batch.gate_simulation_result(result, unlocked=access["full_evidence"])


@router.get("/monitors/{monitor_id}/answer-simulator/estimate")
def simulator_estimate(monitor_id: str, question_count: int = 0,
                       request_llm_step: bool = False,
                       ctx: AuthContext = Depends(require_permission("report:view")),
                       db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    return sim_service.estimate_simulation(question_count, request_llm_step=request_llm_step)
