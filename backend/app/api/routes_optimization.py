"""URL-wise optimization: the overview, the important-URL list, one URL's unified
record, and the fix lifecycle of its recommendations. Org-scoped through
`_owned_monitor` (another organization's website or page is a 404) and Pro-gated like
Page Opportunities. Reading needs report:view; changing an action needs scan:run, so
viewers can see progress but not change it. Nothing here modifies the website.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_permission
from app.api.routes_monitors import _owned_monitor
from app.billing import entitlements
from app.db.session import get_db
from app.monitoring import scheduler
from app.services.url_intelligence import optimization as opt

router = APIRouter(tags=["optimization"])


class ActionChange(BaseModel):
    op: str
    note: str | None = None
    reason: str | None = None


def _guard(db: Session, ctx: AuthContext, monitor_id: str):
    monitor = _owned_monitor(db, ctx, monitor_id)
    if not entitlements.is_pro(db, ctx.org_id):
        raise HTTPException(status_code=402, detail="URL optimization tracking is a Pro feature.")
    return monitor


def _resolve(db: Session, ctx: AuthContext, monitor_id: str, crawl_url_id: str):
    try:
        return opt.resolve_page(db, ctx.org_id, monitor_id, crawl_url_id)
    except opt.ActionError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


@router.get("/monitors/{monitor_id}/optimization")
def get_overview(monitor_id: str, ctx: AuthContext = Depends(require_permission("report:view")),
                 db: Session = Depends(get_db)):
    _guard(db, ctx, monitor_id)
    return opt.overview(db, monitor_id)


@router.get("/monitors/{monitor_id}/optimization/urls")
def list_urls(monitor_id: str, status: str | None = Query(default=None), q: str | None = Query(default=None),
              priority: str | None = Query(default=None), page: int = Query(default=1, ge=1),
              page_size: int = Query(default=25, ge=1, le=100),
              ctx: AuthContext = Depends(require_permission("report:view")), db: Session = Depends(get_db)):
    _guard(db, ctx, monitor_id)
    return opt.url_list(db, monitor_id, status=status, q=q, priority=priority, page=page, page_size=page_size)


@router.get("/monitors/{monitor_id}/optimization/urls/{crawl_url_id}")
def get_record(monitor_id: str, crawl_url_id: str, ctx: AuthContext = Depends(require_permission("report:view")),
               db: Session = Depends(get_db)):
    _guard(db, ctx, monitor_id)
    run, score, row = _resolve(db, ctx, monitor_id, crawl_url_id)
    return opt.page_record(db, run, score, row)


@router.post("/monitors/{monitor_id}/optimization/urls/{crawl_url_id}/actions/{code}")
def change_action(monitor_id: str, crawl_url_id: str, code: str, body: ActionChange,
                  ctx: AuthContext = Depends(require_permission("scan:run")), db: Session = Depends(get_db)):
    """Start, mark ready for verification, request or cancel a re-check, ignore (with a
    reason), reopen, or add a note. Marking ready never verifies: it queues a check."""
    monitor = _guard(db, ctx, monitor_id)
    run, score, row = _resolve(db, ctx, monitor_id, crawl_url_id)
    try:
        action = opt.get_or_create_action(db, run=run, score=score, row=row, code=code, user_id=ctx.user.id)
        opt.apply_op(db, action, body.op, user_id=ctx.user.id, score=score, note=body.note, reason=body.reason)
    except opt.ActionError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    if action.next_check_at is not None and body.op in ("ready", "recheck"):
        scheduler.enqueue_url_recheck(db, monitor.id, monitor.organization_id)
    return opt.page_record(db, run, score, row)


@router.post("/monitors/{monitor_id}/optimization/rechecks/cancel")
def cancel_rechecks(monitor_id: str, ctx: AuthContext = Depends(require_permission("scan:run")),
                    db: Session = Depends(get_db)):
    _guard(db, ctx, monitor_id)
    return {"cancelled": opt.cancel_scheduled_checks(db, monitor_id, ctx.user.id)}
