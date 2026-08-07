"""Audit logging (Phase 8). Records privileged admin actions — logins, deletions,
permission and settings changes, exports — for the /admin/logs view. Best-effort:
recording never raises into the calling endpoint."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import AuditLog

logger = logging.getLogger("aeomirror.audit")

# Canonical action names (also fine to pass any string).
ADMIN_LOGIN = "admin_login"
DELETE_USER = "delete_user"
SUSPEND_USER = "suspend_user"
ACTIVATE_USER = "activate_user"
RESET_PASSWORD = "reset_password"
VERIFY_EMAIL = "verify_email"
DELETE_ORG = "delete_organization"
DELETE_SCAN = "delete_scan"
RERUN_SCAN = "rerun_scan"
DELETE_MONITOR = "delete_monitor"
PAUSE_MONITOR = "pause_monitor"
RESUME_MONITOR = "resume_monitor"
RUN_MONITOR = "run_monitor"
PERMISSION_CHANGE = "permission_change"
SETTINGS_CHANGE = "settings_change"
FLAG_CHANGE = "feature_flag_change"
EXPORT = "export"
CONTACT_STATUS_CHANGE = "contact_status_change"
DELETE_CONTACT = "delete_contact"


def record(db: Session, *, actor=None, action: str, target_type: str | None = None,
           target_id: str | None = None, meta: dict | None = None,
           ip_hash: str | None = None) -> None:
    try:
        db.add(AuditLog(
            actor_id=getattr(actor, "id", None),
            actor_email=getattr(actor, "email", None),
            action=action, target_type=target_type, target_id=target_id,
            meta=meta or {}, ip_hash=ip_hash,
        ))
        db.commit()
    except Exception as e:  # audit must never break the action it records
        logger.warning("audit record failed (%s): %s", action, type(e).__name__)
        db.rollback()


def query(db: Session, *, action: str | None = None, actor_id: str | None = None,
          target_type: str | None = None, limit: int = 100, offset: int = 0):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if actor_id:
        q = q.filter(AuditLog.actor_id == actor_id)
    if target_type:
        q = q.filter(AuditLog.target_type == target_type)
    total = q.count()
    rows = q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()
    return rows, total
