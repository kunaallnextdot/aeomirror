"""System settings (Phase 8): a small key/value store for global admin config —
scanner version, default rubric, email templates, maintenance mode, feature flags
and free-form application configuration. DB rows override the code defaults."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import SystemSetting
from app.scanner.signals.base import SCANNER_VERSION

# Keys the admin UI knows about, with safe defaults.
DEFAULTS: dict = {
    "scanner_version": SCANNER_VERSION,
    "default_rubric": None,          # resolved from the active rubric when unset
    "maintenance_mode": False,
    "maintenance_message": "AEOMirror is undergoing scheduled maintenance. Please try again shortly.",
    "email_from_name": "AEOMirror",
    "email_templates": {
        "welcome_subject": "Welcome to AEOMirror",
        "alert_subject": "AEOMirror alert",
        "summary_subject": "Your AEOMirror summary",
    },
    "app_config": {
        "default_monitor_frequency": "weekly",
    },
}

# Keys the admin PATCH is allowed to write (protects internal keys).
WRITABLE_KEYS = set(DEFAULTS.keys())


def get(db: Session, key: str, default=None):
    row = db.get(SystemSetting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key, default)


def get_all(db: Session) -> dict:
    merged = dict(DEFAULTS)
    for row in db.query(SystemSetting).all():
        merged[row.key] = row.value
    return merged


def set_value(db: Session, key: str, value, actor_id: str | None = None) -> None:
    row = db.get(SystemSetting, key)
    if row:
        row.value = value
        row.updated_by = actor_id
        row.updated_at = datetime.utcnow()
    else:
        db.add(SystemSetting(key=key, value=value, updated_by=actor_id))
    db.commit()


def set_many(db: Session, values: dict, actor_id: str | None = None) -> dict:
    applied = {}
    for key, value in (values or {}).items():
        if key not in WRITABLE_KEYS:
            continue
        set_value(db, key, value, actor_id)
        applied[key] = value
    return applied


def is_maintenance(db: Session) -> bool:
    return bool(get(db, "maintenance_mode", False))
