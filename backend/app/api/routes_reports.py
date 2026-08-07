"""Report endpoints (Phase 6): the actionable AI-visibility report + exports.

All endpoints are authenticated and org-scoped (a scan outside your org is 404).
The report JSON is built by the rule-based engine and cached in `reports`; PDF/CSV
are generated on demand at download time — never during a scan. PDF generation is
CPU-bound, so it runs in a threadpool (`run_in_threadpool`) and does not block the
event loop. This is the seam where a future background-job queue would slot in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import AuthContext, is_platform_admin, require_permission
from app.config import settings
from app.db.models import Scan
from app.db.session import get_db
from app.reports import shares
from app.reports.exporters import to_csv_bytes, to_json_bytes
from app.reports.pdf import build_pdf
from app.reports.service import generate_report, record_export

router = APIRouter(prefix="/reports", tags=["reports"])


def _share_public(row) -> dict:
    """Share info returned to the OWNER — includes the token + path so they can retrieve
    and re-copy their own share URL. Never includes a viewer IP (not stored)."""
    return {
        "id": row.id, "scan_id": row.scan_id,
        "token": row.token, "path": f"/r/{row.token}",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "view_count": row.view_count or 0,
        "last_viewed_at": row.last_viewed_at.isoformat() if row.last_viewed_at else None,
    }


def _owned_scan(db: Session, ctx: AuthContext, scan_id: str) -> Scan:
    row = db.get(Scan, scan_id)
    # 404 (not 403) so a scan outside your org is indistinguishable from missing.
    if not row or row.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Scan not found.")
    return row


def _safe_domain(report: dict) -> str:
    dom = (report.get("domain") or "report").replace("/", "-").replace(":", "")
    return dom or "report"


def _export_gate(db: Session, ctx: AuthContext, scan_id: str) -> JSONResponse | None:
    """Billing gate for report exports (PDF/CSV/JSON): allowed for Pro or a one-time $9
    report purchase of this scan. Returns a 402 JSONResponse (with an `unlock` hint the
    client turns into a purchase) when locked, else None. Viewing the on-screen report
    stays free."""
    from app.billing import entitlements
    if entitlements.export_unlocked(db, ctx.org_id, scan_id):
        return None
    return JSONResponse(status_code=402, content={
        "detail": "Unlock this report's exports for $9, or go Pro.",
        "unlock": {"kind": "report", "scan_id": scan_id},
    })


@router.get("/{scan_id}")
def get_report(scan_id: str, refresh_ai: int = 0,
               ctx: AuthContext = Depends(require_permission("report:view")),
               db: Session = Depends(get_db)):
    """The AI-visibility report (JSON) for a scan. Viewing the on-screen report is free.
    An AI-written narrative is merged under report["ai"] for ALL plans: the paid tier
    (Pro org or a $9-unlocked report) gets full insights + an action plan, the free tier
    gets the summary + top-3 insights (subject to the free guardrails). Exports stay
    gated. `?refresh_ai=1` forces regeneration (admin / non-production only)."""
    scan = _owned_scan(db, ctx, scan_id)
    allow_refresh = bool(refresh_ai) and (not settings.is_production or is_platform_admin(ctx.user))
    report, _row = generate_report(db, scan, user=ctx.user, refresh_ai=allow_refresh)
    return report


@router.get("/{scan_id}/access")
def report_access(scan_id: str,
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    """Lightweight unlock check for the report's exports: {unlocked: bool}. Lets the
    client decide download-vs-purchase without triggering a 402 first."""
    _owned_scan(db, ctx, scan_id)   # 404 for a scan outside the org
    from app.billing import entitlements
    return {"unlocked": entitlements.export_unlocked(db, ctx.org_id, scan_id)}


@router.get("/{scan_id}/json")
def download_json(scan_id: str,
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    scan = _owned_scan(db, ctx, scan_id)
    gate = _export_gate(db, ctx, scan_id)
    if gate:
        return gate
    # Reaching here means exports are unlocked → the caller qualifies for the paid narrative.
    report, row = generate_report(db, scan, user=ctx.user)
    body = to_json_bytes(report)
    record_export(db, report_id=row.id, scan_row=scan, user_id=ctx.user.id,
                  fmt="json", size=len(body))
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="aeomirror-report-{_safe_domain(report)}.json"'})


@router.get("/{scan_id}/csv")
def download_csv(scan_id: str,
                 ctx: AuthContext = Depends(require_permission("report:view")),
                 db: Session = Depends(get_db)):
    scan = _owned_scan(db, ctx, scan_id)
    gate = _export_gate(db, ctx, scan_id)
    if gate:
        return gate
    report, row = generate_report(db, scan, user=ctx.user)
    body = to_csv_bytes(report)
    record_export(db, report_id=row.id, scan_row=scan, user_id=ctx.user.id,
                  fmt="csv", size=len(body))
    return Response(
        content=body, media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="aeomirror-report-{_safe_domain(report)}.csv"'})


@router.get("/{scan_id}/pdf")
async def download_pdf(scan_id: str,
                       ctx: AuthContext = Depends(require_permission("report:view")),
                       db: Session = Depends(get_db)):
    from app.admin import flags
    if not flags.is_enabled(db, "pdf_export"):
        raise HTTPException(status_code=403, detail="PDF export is currently disabled.")
    scan = _owned_scan(db, ctx, scan_id)
    gate = _export_gate(db, ctx, scan_id)
    if gate:
        return gate
    # generate_report may call the AI narrative on first generation — run it (and the
    # CPU-bound PDF render) off the event loop so scans/requests are never blocked.
    report, row = await run_in_threadpool(generate_report, db, scan, user=ctx.user)
    body = await run_in_threadpool(build_pdf, report)
    record_export(db, report_id=row.id, scan_row=scan, user_id=ctx.user.id,
                  fmt="pdf", size=len(body))
    return Response(
        content=body, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="aeomirror-report-{_safe_domain(report)}.pdf"'})


# ------------------------------- public share links -------------------------------
# Creating/listing/revoking a share requires only report:view (any org member who can
# view the report). It is NOT export-gated: the share link is a distribution channel
# (its point is the "Scanned with AEOMirror" backlink), and the public page serves only
# the free-viewable report with no exports. The active-share COUNT is capped per plan.
@router.post("/{scan_id}/share")
def create_report_share(scan_id: str,
                        ctx: AuthContext = Depends(require_permission("report:view")),
                        db: Session = Depends(get_db)):
    """Mint a public read-only share link for a scan's report. Returns the share incl.
    its token/path; the URL stays retrievable via the list endpoint. 402 when the org's
    active-share cap is reached."""
    _owned_scan(db, ctx, scan_id)   # 404 for a scan outside the org
    from app.billing import entitlements
    q = entitlements.share_quota(db, ctx.org_id)
    if not q["unlimited"] and q["remaining"] <= 0:
        return JSONResponse(status_code=402, content={
            "detail": f"You've reached your limit of {q['limit']} active share links. "
                      "Revoke one, or go Pro for unlimited.",
            "unlock": {"kind": "share"},
        })
    row, _raw = shares.create_share(
        db, scan_id=scan_id, org_id=ctx.org_id, user_id=ctx.user.id,
        ttl_seconds=settings.report_share_ttl_seconds)
    return _share_public(row)   # includes token + path; retrievable again via the list endpoint


@router.get("/{scan_id}/shares")
def list_report_shares(scan_id: str,
                       ctx: AuthContext = Depends(require_permission("report:view")),
                       db: Session = Depends(get_db)):
    """Active (unrevoked, unexpired) share links for this scan, incl. the token/path so
    the owner can re-copy the URL. Used by the report view to show/manage a link."""
    _owned_scan(db, ctx, scan_id)
    rows = [r for r in shares.active_shares(db, ctx.org_id) if r.scan_id == scan_id]
    return {"shares": [_share_public(r) for r in rows]}


@router.delete("/shares/{share_id}")
def revoke_report_share(share_id: str,
                        ctx: AuthContext = Depends(require_permission("report:view")),
                        db: Session = Depends(get_db)):
    """Revoke one of the org's share links (the public URL stops resolving immediately)."""
    if not shares.revoke_share(db, share_id=share_id, org_id=ctx.org_id):
        raise HTTPException(status_code=404, detail="Share not found.")
    return {"revoked": True}
