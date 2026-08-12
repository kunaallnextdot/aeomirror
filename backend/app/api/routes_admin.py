"""Admin platform API (Phase 8). Every endpoint requires a platform admin
(`get_admin`) and is NOT visible to customers. Provides the admin dashboard,
user / organization / scan / monitor management, analytics, system health, audit
logs, settings and feature flags. Privileged mutations are audit-logged.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.admin import analytics as analytics_svc
from app.admin import audit, flags, health
from app.admin import settings_store as settings_svc
from app.api.deps import get_admin
from app.api.routes_scan import _client_ip, _ip_hash, _normalize, run_scan
from app.core.observability import metrics
from app.core.security import generate_token, hash_token, token_expiry
from app.core.ssrf import UnsafeUrlError, validate_url
from app.db.models import (
    ALERT_OPEN, CONTACT_STATUSES, JOB_FAILED, MONITOR_ACTIVE, MONITOR_PAUSED,
    AiContentInsight, Alert, Contact, EmailVerification, Invitation, Monitor,
    MonitorHistory, NotificationLog, Organization, OrganizationMember, PasswordReset,
    Report, ReportExport, ReportShare, Scan, ScanSnapshot, ScheduledJob,
    Session as SessionModel, UsageEvent, User,
)
from app.db.session import get_db
from app.monitoring import runner, scheduler
from app.reports.engine import build_report
from app.reports.service import scan_to_input
from app.schemas.admin import UpdateSettingsRequest, UserActionResult
from app.schemas.contact import ContactActionResult, ContactStatusUpdate

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_admin)])


# ------------------------------- helpers -------------------------------
def _page(query, page: int, page_size: int):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size}


def _audit_ctx(request: Request):
    return _ip_hash(_client_ip(request))


def _user_row(db: Session, u: User) -> dict:
    member = db.query(OrganizationMember).filter(OrganizationMember.user_id == u.id).first()
    org = db.get(Organization, member.organization_id) if member else None
    return {
        "id": u.id, "name": u.name, "email": u.email, "status": u.status,
        "role": u.role, "email_verified": u.email_verified,
        "is_platform_admin": bool(u.is_platform_admin),
        "organization": {"id": org.id, "name": org.name} if org else None,
        "created_at": u.created_at, "last_login": u.last_login,
    }


def _org_row(db: Session, o: Organization) -> dict:
    from app.billing import entitlements
    members = db.query(func.count(OrganizationMember.id)).filter(OrganizationMember.organization_id == o.id).scalar() or 0
    monitors = db.query(func.count(Monitor.id)).filter(Monitor.organization_id == o.id).scalar() or 0
    scans = db.query(func.count(Scan.id)).filter(Scan.organization_id == o.id).scalar() or 0
    export_bytes = db.query(func.coalesce(func.sum(ReportExport.size_bytes), 0)).filter(ReportExport.organization_id == o.id).scalar() or 0
    return {
        "id": o.id, "name": o.name, "slug": o.slug, "owner_id": o.owner_id,
        "created_at": o.created_at,
        "members": members, "monitors": monitors, "scans": scans,
        "storage_bytes": int(export_bytes),
        # Real plan from an active subscription (one bounded query per row; the list is
        # paginated so this stays small). Falls back to "free" when there is none.
        "plan": entitlements.current_plan(db, o.id),
    }


def _scan_row(s: Scan) -> dict:
    r = s.result or {}
    return {
        "id": s.id, "url": s.url, "domain": _normalize(s.url),
        "ars": s.ars, "overall_score": r.get("overall_score"),
        "organization_id": s.organization_id, "user_id": s.user_id,
        "scanner_version": r.get("scanner_version"),
        "duration_ms": r.get("duration_ms"), "created_at": s.created_at,
    }


def _monitor_row(db: Session, m: Monitor) -> dict:
    open_alerts = db.query(func.count(Alert.id)).filter(Alert.monitor_id == m.id, Alert.status == ALERT_OPEN).scalar() or 0
    return {
        "id": m.id, "url": m.url, "domain": m.normalized_url, "name": m.name,
        "organization_id": m.organization_id, "frequency": m.frequency,
        "status": m.status, "latest_score": m.latest_score,
        "last_scan_at": m.last_scan_at, "next_scan_at": m.next_scan_at,
        "open_alerts": open_alerts, "created_at": m.created_at,
    }


# ================================ dashboard ================================
@router.get("/dashboard")
def admin_dashboard(db: Session = Depends(get_db)):
    return analytics_svc.dashboard_stats(
        db, api_requests=metrics.snapshot().get("api_requests", 0),
        health_status=health.overall_status(db))


# ================================ users ================================
@router.get("/users")
def admin_users(db: Session = Depends(get_db),
                q: str | None = Query(default=None),
                status: str | None = Query(default=None),
                page: int = 1, page_size: int = 25):
    query = db.query(User)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(func.lower(User.email).like(like), func.lower(User.name).like(like)))
    if status:
        query = query.filter(User.status == status)
    query = query.order_by(User.created_at.desc())
    res = _page(query, page, page_size)
    res["items"] = [_user_row(db, u) for u in res["items"]]
    return res


def _get_user(db: Session, user_id: str) -> User:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found.")
    return u


@router.get("/users/{user_id}/activity")
def admin_user_activity(user_id: str, db: Session = Depends(get_db)):
    u = _get_user(db, user_id)
    scans = db.query(func.count(Scan.id)).filter(Scan.user_id == u.id).scalar() or 0
    monitors = db.query(func.count(Monitor.id)).filter(Monitor.user_id == u.id).scalar() or 0
    sessions = db.query(func.count(SessionModel.id)).filter(SessionModel.user_id == u.id, SessionModel.revoked == False).scalar() or 0  # noqa: E712
    recent = (db.query(Scan).filter(Scan.user_id == u.id).order_by(Scan.created_at.desc()).limit(10).all())
    return {"user": _user_row(db, u), "scan_count": scans, "monitor_count": monitors,
            "active_sessions": sessions, "recent_scans": [_scan_row(s) for s in recent]}


@router.post("/users/{user_id}/suspend", response_model=UserActionResult)
def admin_suspend_user(user_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    u = _get_user(db, user_id)
    u.status = "suspended"
    db.commit()
    audit.record(db, actor=admin, action=audit.SUSPEND_USER, target_type="user", target_id=u.id, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": u.id, "status": u.status}


@router.post("/users/{user_id}/activate", response_model=UserActionResult)
def admin_activate_user(user_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    u = _get_user(db, user_id)
    u.status = "active"
    db.commit()
    audit.record(db, actor=admin, action=audit.ACTIVATE_USER, target_type="user", target_id=u.id, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": u.id, "status": u.status}


@router.post("/users/{user_id}/verify-email", response_model=UserActionResult)
def admin_verify_email(user_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    u = _get_user(db, user_id)
    u.email_verified = True
    db.commit()
    audit.record(db, actor=admin, action=audit.VERIFY_EMAIL, target_type="user", target_id=u.id, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": u.id, "status": u.status}


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(user_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    from app.config import settings
    from app.services.auth_email import send_password_reset_email
    u = _get_user(db, user_id)
    raw = generate_token()
    db.add(PasswordReset(user_id=u.id, token_hash=hash_token(raw),
                         expires_at=token_expiry(settings.reset_token_ttl_seconds)))
    db.commit()
    send_password_reset_email(u.email, raw)
    audit.record(db, actor=admin, action=audit.RESET_PASSWORD, target_type="user", target_id=u.id, ip_hash=_audit_ctx(request))
    # dev-only: surface the token when email isn't configured
    return {"ok": True, "id": u.id,
            "dev_reset_token": raw if not settings.is_production else None}


@router.delete("/users/{user_id}", response_model=UserActionResult)
def admin_delete_user(user_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    u = _get_user(db, user_id)
    if u.id == admin.id:
        raise HTTPException(status_code=409, detail="You cannot delete your own admin account.")
    db.query(OrganizationMember).filter(OrganizationMember.user_id == u.id).delete(synchronize_session=False)
    db.query(SessionModel).filter(SessionModel.user_id == u.id).delete(synchronize_session=False)
    # Clean up the user's auth tokens too, so no reset/verification rows dangle.
    db.query(PasswordReset).filter(PasswordReset.user_id == u.id).delete(synchronize_session=False)
    db.query(EmailVerification).filter(EmailVerification.user_id == u.id).delete(synchronize_session=False)
    email = u.email
    db.delete(u)
    db.commit()
    audit.record(db, actor=admin, action=audit.DELETE_USER, target_type="user", target_id=user_id,
                 meta={"email": email}, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": user_id, "status": "deleted"}


# ================================ organizations ================================
@router.get("/organizations")
def admin_orgs(db: Session = Depends(get_db), q: str | None = Query(default=None),
               page: int = 1, page_size: int = 25):
    query = db.query(Organization)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(func.lower(Organization.name).like(like), func.lower(Organization.slug).like(like)))
    query = query.order_by(Organization.created_at.desc())
    res = _page(query, page, page_size)
    res["items"] = [_org_row(db, o) for o in res["items"]]
    return res


@router.get("/organizations/{org_id}")
def admin_org_detail(org_id: str, db: Session = Depends(get_db)):
    o = db.get(Organization, org_id)
    if not o:
        raise HTTPException(status_code=404, detail="Organization not found.")
    rows = (db.query(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .filter(OrganizationMember.organization_id == o.id).all())
    members = [{"id": m.id, "user_id": u.id, "name": u.name, "email": u.email,
                "role": m.role, "is_owner": u.id == o.owner_id} for m, u in rows]
    return {"organization": _org_row(db, o), "members": members}


@router.delete("/organizations/{org_id}", response_model=UserActionResult)
def admin_delete_org(org_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    o = db.get(Organization, org_id)
    if not o:
        raise HTTPException(status_code=404, detail="Organization not found.")
    # Remove DISPOSABLE org-scoped data (no FK cascade in this schema). Financial
    # records — Payment, Invoice, Subscription — are deliberately RETAINED for
    # accounting / dispute-handling and invoice-number sequence integrity; the admin
    # surface has no cross-org financial aggregate, so they never skew a count.
    for model in (Monitor, MonitorHistory, Alert, Report, ReportExport, ReportShare,
                  ScanSnapshot, OrganizationMember, Scan, UsageEvent, AiContentInsight,
                  Invitation, NotificationLog):
        db.query(model).filter(model.organization_id == o.id).delete(synchronize_session=False)
    db.query(ScheduledJob).filter(ScheduledJob.organization_id == o.id).delete(synchronize_session=False)
    db.delete(o)
    db.commit()
    audit.record(db, actor=admin, action=audit.DELETE_ORG, target_type="organization", target_id=org_id,
                 meta={"name": o.name}, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": org_id, "status": "deleted"}


# ================================ scans ================================
@router.get("/scans")
def admin_scans(db: Session = Depends(get_db), q: str | None = Query(default=None),
                organization_id: str | None = Query(default=None),
                page: int = 1, page_size: int = 25):
    query = db.query(Scan)
    if q:
        query = query.filter(func.lower(Scan.normalized_url).like(f"%{q.lower()}%"))
    if organization_id:
        query = query.filter(Scan.organization_id == organization_id)
    query = query.order_by(Scan.created_at.desc())
    res = _page(query, page, page_size)
    res["items"] = [_scan_row(s) for s in res["items"]]
    return res


@router.get("/scans/{scan_id}")
def admin_scan_report(scan_id: str, db: Session = Depends(get_db)):
    s = db.get(Scan, scan_id)
    if not s:
        raise HTTPException(status_code=404, detail="Scan not found.")
    return {"scan": _scan_row(s), "report": build_report(scan_to_input(s))}


@router.get("/scans/{scan_id}/logs")
def admin_scan_logs(scan_id: str, db: Session = Depends(get_db)):
    """Synthesized scanner logs for a stored scan (no separate log store)."""
    s = db.get(Scan, scan_id)
    if not s:
        raise HTTPException(status_code=404, detail="Scan not found.")
    r = s.result or {}
    logs = [
        f"[{r.get('scanned_at', '')}] scan {s.id} started for {s.url}",
        f"fetch ok · scanner {r.get('scanner_version', '?')} · {r.get('duration_ms', '?')}ms",
        f"overall_score={r.get('overall_score')} ars={s.ars}",
    ]
    for sec in r.get("sections", []):
        logs.append(f"signal {sec.get('id')}: {sec.get('status')} ({sec.get('score')}) "
                    f"— {len(sec.get('issues') or [])} issue(s)")
    logs.append("scan complete")
    return {"scan_id": s.id, "logs": logs}


@router.delete("/scans/{scan_id}", response_model=UserActionResult)
def admin_delete_scan(scan_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    s = db.get(Scan, scan_id)
    if not s:
        raise HTTPException(status_code=404, detail="Scan not found.")
    db.query(Report).filter(Report.scan_id == s.id).delete(synchronize_session=False)
    # A public share points at this scan; drop it so the link stops resolving (it would
    # 404 anyway once the report is gone, but leaving the row would orphan it).
    db.query(ReportShare).filter(ReportShare.scan_id == s.id).delete(synchronize_session=False)
    db.delete(s)
    db.commit()
    audit.record(db, actor=admin, action=audit.DELETE_SCAN, target_type="scan", target_id=scan_id,
                 meta={"url": s.url}, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": scan_id, "status": "deleted"}


@router.post("/scans/{scan_id}/rerun")
async def admin_rerun_scan(scan_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    s = db.get(Scan, scan_id)
    if not s:
        raise HTTPException(status_code=404, detail="Scan not found.")
    try:
        safe_url = validate_url(s.url)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=422, detail=str(e))
    payload = await run_scan(db, safe_url, ip="admin",
                             org_id=s.organization_id, user_id=s.user_id)
    audit.record(db, actor=admin, action=audit.RERUN_SCAN, target_type="scan", target_id=scan_id,
                 meta={"new_scan_id": payload.get("scan_id")}, ip_hash=_audit_ctx(request))
    return {"ok": True, "scan_id": payload.get("scan_id"), "overall_score": payload.get("overall_score")}


# ================================ monitors ================================
@router.get("/monitors")
def admin_monitors(db: Session = Depends(get_db), status: str | None = Query(default=None),
                   page: int = 1, page_size: int = 25):
    query = db.query(Monitor)
    if status:
        query = query.filter(Monitor.status == status)
    query = query.order_by(Monitor.created_at.desc())
    res = _page(query, page, page_size)
    res["items"] = [_monitor_row(db, m) for m in res["items"]]
    return res


def _get_monitor(db: Session, monitor_id: str) -> Monitor:
    m = db.get(Monitor, monitor_id)
    if not m:
        raise HTTPException(status_code=404, detail="Monitor not found.")
    return m


@router.post("/monitors/{monitor_id}/pause", response_model=UserActionResult)
def admin_pause_monitor(monitor_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    m = _get_monitor(db, monitor_id)
    m.status = MONITOR_PAUSED
    m.next_scan_at = None
    db.commit()
    audit.record(db, actor=admin, action=audit.PAUSE_MONITOR, target_type="monitor", target_id=m.id, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": m.id, "status": m.status}


@router.post("/monitors/{monitor_id}/resume", response_model=UserActionResult)
def admin_resume_monitor(monitor_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    m = _get_monitor(db, monitor_id)
    m.status = MONITOR_ACTIVE
    if m.frequency != "manual":
        m.next_scan_at = datetime.utcnow()
    db.commit()
    audit.record(db, actor=admin, action=audit.RESUME_MONITOR, target_type="monitor", target_id=m.id, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": m.id, "status": m.status}


@router.post("/monitors/{monitor_id}/run")
async def admin_run_monitor(monitor_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    m = _get_monitor(db, monitor_id)
    job = scheduler.enqueue_manual(db, m)
    await runner.process_job(db, job)
    db.refresh(m)
    audit.record(db, actor=admin, action=audit.RUN_MONITOR, target_type="monitor", target_id=m.id, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": m.id, "latest_score": m.latest_score, "job_status": job.status}


@router.delete("/monitors/{monitor_id}", response_model=UserActionResult)
def admin_delete_monitor(monitor_id: str, request: Request, admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    m = _get_monitor(db, monitor_id)
    db.query(ScheduledJob).filter(ScheduledJob.monitor_id == m.id).delete(synchronize_session=False)
    db.query(MonitorHistory).filter(MonitorHistory.monitor_id == m.id).delete(synchronize_session=False)
    db.query(Alert).filter(Alert.monitor_id == m.id).delete(synchronize_session=False)
    db.delete(m)
    db.commit()
    audit.record(db, actor=admin, action=audit.DELETE_MONITOR, target_type="monitor", target_id=monitor_id, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": monitor_id, "status": "deleted"}


# ================================ analytics / system / logs ================================
@router.get("/analytics")
def admin_analytics(db: Session = Depends(get_db)):
    return analytics_svc.analytics(db)


@router.get("/system")
async def admin_system(db: Session = Depends(get_db)):
    # Health probes touch the DB + Redis; run off the event loop.
    return await run_in_threadpool(health.system_health, db)


@router.get("/logs")
def admin_logs(db: Session = Depends(get_db), action: str | None = Query(default=None),
               page: int = 1, page_size: int = 50):
    page = max(1, page); page_size = min(200, max(1, page_size))
    rows, total = audit.query(db, action=action, limit=page_size, offset=(page - 1) * page_size)
    items = [{
        "id": r.id, "actor_id": r.actor_id, "actor_email": r.actor_email,
        "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
        "meta": r.meta, "created_at": r.created_at,
    } for r in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size}


# ================================ settings + feature flags ================================
@router.get("/settings")
def admin_get_settings(db: Session = Depends(get_db)):
    return {
        "settings": settings_svc.get_all(db),
        "feature_flags": flags.all_flags(db),
        "writable_keys": sorted(settings_svc.WRITABLE_KEYS),
        "flag_names": sorted(flags.DEFAULT_FLAGS.keys()),
    }


@router.patch("/settings")
def admin_update_settings(body: UpdateSettingsRequest, request: Request,
                          admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    applied_settings = {}
    applied_flags = {}
    if body.settings:
        applied_settings = settings_svc.set_many(db, body.settings, actor_id=admin.id)
        audit.record(db, actor=admin, action=audit.SETTINGS_CHANGE, target_type="settings",
                     meta={"keys": list(applied_settings.keys())}, ip_hash=_audit_ctx(request))
    if body.feature_flags:
        for name, enabled in body.feature_flags.items():
            if name in flags.DEFAULT_FLAGS:
                flags.set_flag(db, name, bool(enabled), actor_id=admin.id)
                applied_flags[name] = bool(enabled)
        if applied_flags:
            audit.record(db, actor=admin, action=audit.FLAG_CHANGE, target_type="feature_flags",
                         meta=applied_flags, ip_hash=_audit_ctx(request))
    return {"ok": True, "settings": settings_svc.get_all(db), "feature_flags": flags.all_flags(db),
            "applied": {"settings": applied_settings, "feature_flags": applied_flags}}


# ================================ support inbox (contacts) ================================
def _contact_row(c: Contact) -> dict:
    return {
        "id": c.id, "name": c.name, "email": c.email, "website": c.website,
        "subject": c.subject, "message": c.message, "status": c.status,
        "created_at": c.created_at,
    }


def _get_contact(db: Session, contact_id: str) -> Contact:
    c = db.get(Contact, contact_id)
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found.")
    return c


@router.get("/contacts")
def admin_contacts(db: Session = Depends(get_db),
                   q: str | None = Query(default=None),
                   status: str | None = Query(default=None),
                   page: int = 1, page_size: int = 25):
    """List support submissions, newest first, with search + status filter."""
    query = db.query(Contact)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(
            func.lower(Contact.email).like(like),
            func.lower(Contact.name).like(like),
            func.lower(Contact.subject).like(like),
        ))
    if status in CONTACT_STATUSES:
        query = query.filter(Contact.status == status)
    query = query.order_by(Contact.created_at.desc())
    res = _page(query, page, page_size)
    # Status tallies power the New / Open / Closed inbox tabs.
    counts = dict(db.query(Contact.status, func.count(Contact.id)).group_by(Contact.status).all())
    res["items"] = [_contact_row(c) for c in res["items"]]
    res["counts"] = {s: int(counts.get(s, 0)) for s in CONTACT_STATUSES}
    return res


@router.get("/contacts/{contact_id}")
def admin_contact_detail(contact_id: str, db: Session = Depends(get_db)):
    return _contact_row(_get_contact(db, contact_id))


@router.post("/contacts/{contact_id}/status", response_model=ContactActionResult)
def admin_contact_set_status(contact_id: str, body: ContactStatusUpdate, request: Request,
                             admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    if body.status not in CONTACT_STATUSES:
        raise HTTPException(status_code=422,
            detail=f"status must be one of {', '.join(CONTACT_STATUSES)}.")
    c = _get_contact(db, contact_id)
    c.status = body.status
    db.commit()
    audit.record(db, actor=admin, action=audit.CONTACT_STATUS_CHANGE, target_type="contact",
                 target_id=c.id, meta={"status": body.status}, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": c.id, "status": c.status}


@router.delete("/contacts/{contact_id}", response_model=ContactActionResult)
def admin_delete_contact(contact_id: str, request: Request,
                         admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    c = _get_contact(db, contact_id)
    db.delete(c)
    db.commit()
    audit.record(db, actor=admin, action=audit.DELETE_CONTACT, target_type="contact",
                 target_id=contact_id, ip_hash=_audit_ctx(request))
    return {"ok": True, "id": contact_id, "status": "deleted"}
