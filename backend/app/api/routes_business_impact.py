"""AI Business Impact dashboard API — reads first-party analytics (and, where
connected, GA4) to answer "is AI visibility work generating measurable business
results?" for a signed-in organization. Org-scoped via AuthContext.org_id on every
query (never a client-supplied organization id); every monitor_id filter is
re-validated as belonging to the caller's own org before use.

Free plan: the summary (sessions, AI share) is visible — explicitly required so a
free user can see what the feature does. Conversions/revenue/landing-page
prioritization are Pro, matching the existing GA4 AI Traffic gate
(routes_integrations.py's `_require_pro`) — same convention, not a new one.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_permission
from app.billing import entitlements
from app.db.models import GA4_STATUS_ACTIVE, GoogleAnalyticsConnection, Monitor
from app.db.session import get_db
from app.services.analytics import aggregation

router = APIRouter(prefix="/business-impact", tags=["analytics"])

_MAX_RANGE_DAYS = 90
_DATE_FMT = "%Y-%m-%d"


def _require_pro(db: Session, ctx: AuthContext) -> None:
    if not entitlements.is_pro(db, ctx.org_id):
        raise HTTPException(status_code=402, detail="AI Business Impact details are a Pro feature.")


def _parse_range(start_date: str | None, end_date: str | None) -> tuple[datetime, datetime]:
    end = datetime.utcnow()
    start = end - timedelta(days=30)
    if end_date:
        try:
            end = datetime.strptime(end_date, _DATE_FMT) + timedelta(days=1)   # inclusive end-of-day
        except ValueError:
            raise HTTPException(status_code=422, detail="end_date must be YYYY-MM-DD.")
    if start_date:
        try:
            start = datetime.strptime(start_date, _DATE_FMT)
        except ValueError:
            raise HTTPException(status_code=422, detail="start_date must be YYYY-MM-DD.")
    if start > end:
        raise HTTPException(status_code=422, detail="start_date must be before end_date.")
    if (end - start).days > _MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail=f"Date range cannot exceed {_MAX_RANGE_DAYS} days.")
    return start, end


def _owned_monitor_ids(db: Session, ctx: AuthContext, monitor_id: str | None) -> list[str] | None:
    """None means "every monitor in this org" (no filter) — NEVER a client-supplied
    list; a single requested monitor_id is validated as belonging to this org first."""
    if not monitor_id:
        return None
    owned = (db.query(Monitor.id)
            .filter(Monitor.id == monitor_id, Monitor.organization_id == ctx.org_id).first())
    if not owned:
        raise HTTPException(status_code=404, detail="Site not found.")
    return [monitor_id]


def _has_ga4(db: Session, ctx: AuthContext) -> bool:
    return (db.query(GoogleAnalyticsConnection.id)
           .filter(GoogleAnalyticsConnection.organization_id == ctx.org_id,
                    GoogleAnalyticsConnection.status == GA4_STATUS_ACTIVE).first() is not None)


@router.get("/summary")
def summary(start_date: str | None = Query(default=None), end_date: str | None = Query(default=None),
           monitor_id: str | None = Query(default=None),
           ctx: AuthContext = Depends(require_permission("report:view")),
           db: Session = Depends(get_db)):
    start, end = _parse_range(start_date, end_date)
    monitor_ids = _owned_monitor_ids(db, ctx, monitor_id)
    data = aggregation.summary(db, org_id=ctx.org_id, monitor_ids=monitor_ids, start=start, end=end)
    is_pro = entitlements.is_pro(db, ctx.org_id)
    if not is_pro:
        # Free: show traffic share (the "is this worth upgrading for" signal), but
        # never a conversion/revenue number — never a fabricated lock-teaser value.
        data["ai_conversions"] = None
        data["ai_conversion_rate_pct"] = None
        data["ai_revenue_cents"] = None
        data["confirmed_bookings"] = None
        data["unverified_bookings"] = None
        data["leads"] = None
        data["unverified_leads"] = None
    return {
        "range": {"start": start.date().isoformat(), "end": (end - timedelta(days=1)).date().isoformat()},
        "is_pro": is_pro,
        "ga4_connected": _has_ga4(db, ctx),
        "data_source": "first_party",
        **data,
    }


@router.get("/traffic-over-time")
def traffic_over_time(start_date: str | None = Query(default=None), end_date: str | None = Query(default=None),
                      monitor_id: str | None = Query(default=None), tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
                      ctx: AuthContext = Depends(require_permission("report:view")),
                      db: Session = Depends(get_db)):
    start, end = _parse_range(start_date, end_date)
    monitor_ids = _owned_monitor_ids(db, ctx, monitor_id)
    series = aggregation.traffic_over_time(db, org_id=ctx.org_id, monitor_ids=monitor_ids,
                                           start=start, end=end, tz_offset_minutes=tz_offset_minutes)
    return {"series": series}


@router.get("/channels")
def channels(start_date: str | None = Query(default=None), end_date: str | None = Query(default=None),
            monitor_id: str | None = Query(default=None),
            ctx: AuthContext = Depends(require_permission("report:view")),
            db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    start, end = _parse_range(start_date, end_date)
    monitor_ids = _owned_monitor_ids(db, ctx, monitor_id)
    return {"channels": aggregation.channel_breakdown(db, org_id=ctx.org_id, monitor_ids=monitor_ids,
                                                       start=start, end=end)}


@router.get("/landing-pages")
def landing_pages(start_date: str | None = Query(default=None), end_date: str | None = Query(default=None),
                  monitor_id: str | None = Query(default=None),
                  limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0),
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    start, end = _parse_range(start_date, end_date)
    monitor_ids = _owned_monitor_ids(db, ctx, monitor_id)
    pages = aggregation.landing_page_performance(db, org_id=ctx.org_id, monitor_ids=monitor_ids,
                                                  start=start, end=end, limit=limit, offset=offset)
    return {"pages": pages, "limit": limit, "offset": offset}
