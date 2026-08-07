"""Public, unauthenticated report read path (Phase: shareable links).

This router is deliberately isolated from the org-scoped reports router: it NEVER
imports or calls `_owned_scan`, and it resolves a scan ONLY through a valid, unexpired,
unrevoked share token. It reads the PERSISTED report and never regenerates it or its AI
narrative. The response is a hand-whitelisted payload (app.reports.shares), sent with
X-Robots-Tag: noindex so a privately-shared report can't drift into search results.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.routes_scan import _client_ip, _ip_hash
from app.config import settings
from app.core.cache import public_report_limiter
from app.db.session import get_db
from app.reports import shares

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/reports/{token}")
def public_report(token: str, request: Request, response: Response,
                  db: Session = Depends(get_db)):
    """Serve a shared report by token, or 404. Invalid / expired / revoked are all 404
    and indistinguishable. No auth, no exports, no report regeneration."""
    # Anti-scraping per-IP limit (token entropy already makes guessing infeasible).
    allowed, _remaining, retry_after = public_report_limiter.check(_ip_hash(_client_ip(request)))
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many requests.",
                            headers={"Retry-After": str(retry_after)})

    share = shares.resolve_valid_share(db, token)
    if not share:
        raise HTTPException(status_code=404, detail="Not found.")

    # Read the persisted report ONLY — the public path must never trigger generation or
    # the AI narrative (which would cost tokens / mutate state for an anonymous viewer).
    report_row = shares.latest_persisted_report(db, share.scan_id)
    if not report_row or not report_row.data:
        raise HTTPException(status_code=404, detail="Not found.")

    shares.record_view(db, share)
    response.headers["X-Robots-Tag"] = "noindex"
    return shares.build_public_payload(report_row.data,
                                       include_ai=settings.public_report_include_ai)
